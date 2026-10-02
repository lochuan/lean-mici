"""eagled perception core: the fusion chain behind the eagle.

Turns raw sensors into the per-frame target picture: wide-road camera frame
-> YOLO ROI inference -> ground-plane projection into the car frame -> radar
association -> ``fuse_objects``. No cereal, no params, no decisions here —
``EagleDaemon`` drives :class:`PerceptionCore` once per 5Hz tick and
publishes what it produces (``eagleState`` for consumers, ``eagleDebug``
for the lanlink UI and calibration); the lane-offset planner
(``lane_offset``) is the first in-process consumer of :class:`PerceptionFrame`.
desire_helper in modeld (lane-change gating) reads the published ``eagleState``
instead.

When the camera stream, calibration or the YOLO weights are unavailable the
core degrades to radar-only: the reason is logged once and the frame keeps
publishing with radar-fused targets only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Protocol
import os
import threading
import time

from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.association import associate
from openpilot.selfdrive.eagled.camera_stream import CameraStream
from openpilot.selfdrive.eagled.lane_offset import LaneGeometry, lane_geometry
from openpilot.selfdrive.eagled.projection import calibrated_geometry_from_msg, horizon_row_for, project_detections
from openpilot.selfdrive.eagled.yolo_detector import YoloDetector

YOLO_PKL_PATH = Path(__file__).parent / "models" / "yolo_tinygrad.pkl"

# 视觉检测持有时段：两次推理之间沿用上次检测的最长时间。推理节拍由设备遥测
# 节流（device_health.DeviceHealth）拉长(0.2s~1.5s),与消息新鲜度
# （common.stream_gate）是两个概念，勿混。
# 没有保持的话 eagleDebug 的视觉目标在间隙里被纯雷达帧清空 —— lanlink 上
# 车/人/自行车看起来消失(a4abb7624 回归)。1s 覆盖了大部分节拍间隙,同时
# 把陈旧目标的寿命封顶。
VISION_HOLD_TTL_S = 1.0


class RadarPoint(Protocol):
  dRel: float
  yRel: float
  vRel: float


@dataclass(frozen=True)
class Target:
  dRel: float  # m, longitudinal distance
  yRel: float  # m, lateral position, left positive
  cls: str | None = None  # 视觉类别（car/person/...；纯雷达未关联为 None）
  vRel: float | None = None  # 纵向相对速度 m/s（雷达实测;视觉独有目标速度未知 = None）


def radar_point_key(point) -> int:
  """Stable identity key for a radar point across re-iteration.

  pycapnp constructs a fresh wrapper object on every access to
  ``radarTracks.points``, so ``id()`` differs between the pass that built the
  association pairs and any later pass over the same message -- on device an
  ``id()`` key never matches. ``trackId`` is the identity-stable key (required
  UInt64, no reuse, per car.capnp); ``id()`` remains only as the fallback for
  duck-typed fixtures that carry no ``trackId``. All confirmation-key building
  (fuse_objects and every call site) goes through this helper.
  """
  track_id = getattr(point, "trackId", None)
  return id(point) if track_id is None else int(track_id)


def radar_point_counts(point, v_ego: float, confirmed_keys) -> bool:
  """雷达点是否计入融合:对地静止(或速度缺失按静止)的点须有视觉关联确认。

  fuse_objects 与遥测 _target_rows 共用:遥测不许把被融合丢弃的点标成有压力。
  """
  v_rel = getattr(point, "vRel", None)
  ground_speed = None if v_rel is None else abs(float(v_rel) + v_ego)
  return not (ground_speed is None or ground_speed < C.STATIC_SPEED_THRESH) or radar_point_key(point) in confirmed_keys


def fuse_objects(radar_points: Iterable[RadarPoint], detections: Iterable[dict] | None = None,
                 v_ego: float = 0.0, confirmed_keys: Iterable[int] = (),
                 vision_cls_by_key: dict[int, str] | None = None) -> list[Target]:
  """All fused objects: radar points (confirmed ones take the vision class) + detections.

  Static radar points without vision confirmation are dropped entirely (a phantom
  guardrail must not push the car around). Vision-only detections carry no
  longitudinal speed (``vRel=None``).
  """
  confirmed = set(confirmed_keys)
  cls_by_key = vision_cls_by_key or {}
  objects: list[Target] = []
  for point in radar_points:
    if not radar_point_counts(point, v_ego, confirmed):
      continue
    v_rel = getattr(point, "vRel", None)
    objects.append(Target(dRel=float(point.dRel), yRel=float(point.yRel), cls=cls_by_key.get(radar_point_key(point)),
                          vRel=float(v_rel) if v_rel is not None else None))
  for det in detections or []:
    try:
      dRel, yRel = float(det["dRel"]), float(det["yRel"])
    except (KeyError, TypeError, ValueError):
      continue
    objects.append(Target(dRel=dRel, yRel=yRel, cls=det.get("cls")))
  return objects


@dataclass
class PerceptionFrame:
  """One 5Hz fusion output: everything the publishers and the planner need."""
  radar_points: list          # materialized list of this tick's radar points
  detections: list[dict]      # car-frame projected YOLO detections
  n_associated: int
  pairs: list                 # (radar_point, det, pair_id, cls) from associate()
  confirmed_keys: tuple
  vision_cls_by_key: dict
  objects: list[Target] = None  # fused objects (planner + change-clear input)
  lane_geo: LaneGeometry | None = None  # C2/C7 geometry + quality flags (None = 模型几何不可用)

  def __post_init__(self):
    if self.objects is None:
      self.objects = []


class VisionWorker:
  """单一在途推理的执行器:主循环 submit 不阻塞,poll 取回结果。

  视觉帧(取帧+转换+YOLO)在主循环里占 ~390ms,Ratekeeper 追帧会让
  lateralManeuverPlan 的间隔呈 4ms/395ms 锯齿(2026-09-25 路测)。工作线程
  接走推理,主循环每拍只做融合+发布。邮箱只留一份待跑任务/一份最新结果:
  主循环节奏(0.2-1.5s)远慢于推理(~0.11s),积压不该发生;真积压时最新优先。
  """

  def __init__(self, cores=None):
    self._cores = cores
    self._lock = threading.Lock()
    self._job = None
    self._result = None
    self._stop = threading.Event()
    self._thread = threading.Thread(target=self._run, daemon=True, name="eagled-vision")
    self._thread.start()

  def submit(self, job) -> None:
    with self._lock:
      self._job = job

  def poll(self):
    """取走已完成的结果(只取一次);无结果返回 None。"""
    with self._lock:
      res, self._result = self._result, None
    return res

  def stop(self) -> None:
    self._stop.set()
    self._thread.join(timeout=2.0)

  def _run(self) -> None:
    if self._cores is not None:
      # 自钉安全核:正常靠创建时继承(config_best_effort_process 已钉主线程),
      # 这里显式再钉一次 —— 构造顺序若被改,继承掩码可能失守。core 1 是
      # sensord(FIFO 1)的核,任何线程踏上去都可能把 IMU 发布拖过 100ms 门。
      try:
        os.sched_setaffinity(0, set(self._cores))
      except (OSError, AttributeError):
        pass   # 非 Linux(开发机测试)或掩码不可设:靠继承
    while not self._stop.is_set():
      with self._lock:
        job, self._job = self._job, None
      if job is None:
        time.sleep(0.005)
        continue
      try:
        res = job()
      except Exception:
        cloudlog.exception("eagled: vision worker job failed")
        res = None
      with self._lock:
        self._result = res


class PerceptionCore:
  """Stateful fusion chain: lazy camera/YOLO lifecycle + radar-only degrade."""

  def __init__(self, camera=None, detector=None, camera_factory=CameraStream,
               vision_worker: VisionWorker | None = None):
    self.camera = camera                  # lazy: created via camera_factory on first use
    self.camera_factory = camera_factory
    self.detector = detector              # lazy: YoloDetector on first use
    self.detector_dead = False            # YOLO failed hard -> stop retrying
    self.degraded: set[str] = set()       # radar-only fallback reasons, logged once each
    self.vision_state = "ok"              # eagleState.visionState of the latest tick (current, unlike ``degraded``)
    self._detect_state = "ok"             # outcome of the latest detect(); runs on the worker thread in async mode
    self.last_vision_duration_s = 0.0     # wall time of the last detector forward (0 if skipped)
    self._held: list[dict] = []           # last inference's projected detections
    self._held_t: float = 0.0             # when those detections were projected
    self._worker = vision_worker          # None = 同步执行(单测/影子工具)
    self._hold_gen = 0                    # 清 hold 时 +1,作废在途推理结果

  def _degrade(self, reason: str) -> None:
    """Log a radar-only fallback reason once (never spam)."""
    if reason not in self.degraded:
      self.degraded.add(reason)
      cloudlog.warning(f"eagled: {reason} unavailable, radar-only fallback")

  def _store_hold(self, detections: list[dict], now: float) -> None:
    """Replace the held detections with this tick's outcome (possibly empty)."""
    self._held = list(detections)
    self._held_t = now

  def _submit_vision(self, sm, now: float, camera_to_front: float) -> None:
    """把 due 拍的推理交给工作线程;hold 锚点用取帧时刻 now。"""
    extr = sm['extrinsicsCalibration']
    valid = sm.valid['extrinsicsCalibration']
    gen = self._hold_gen

    def job():
      dets = self.detect(extr, valid, now, camera_to_front)
      return (gen, dets, now, self.last_vision_duration_s)

    self._worker.submit(job)

  def _absorb_worker(self) -> None:
    """把工作线程完成的推理结果落库为新的 hold。过期代际(期间被关闭)丢弃。"""
    if self._worker is None:
      return
    res = self._worker.poll()
    if res is None:
      return
    gen, dets, now, duration = res
    if gen != self._hold_gen:
      # 期间发生了 disable 清空:结果作废。detect 的副作用把 duration 写在了
      # 工作线程上,这里一并归零(保持关闭语义:回到纯雷达且无推理耗时)。
      self.last_vision_duration_s = 0.0
      return
    self._store_hold(dets, now)
    self.last_vision_duration_s = duration

  def _held_detections(self, now: float, v_ego: float) -> list[dict]:
    """视觉检测持有时段内沿用上次成功推理的检测,dRel 按自车速度补偿。

    （这是推理间隙的检测缓存保鲜期,不是消息新鲜度——后者见
    common.stream_gate，两者概念不同，勿混。）
    持有的是上次推理时刻的车体坐标:自车前进 vEgo·age 后目标更近,纵向
    dRel 同步收缩;bearing 是 dRel/yRel 的派生量(association 的匹配键),
    一并重算。补偿后越过保险杠(dRel <= 0)的目标已经从旁边过去了,丢弃
    而不是报负距离。yRel 不补偿:自车横向速度不可知(变道中),1s 内的
    横向漂移远小于 dRel 的老化误差。
    """
    age = now - self._held_t
    if not self._held or age > VISION_HOLD_TTL_S:
      return []
    out: list[dict] = []
    for det in self._held:
      d_rel = det["dRel"] - v_ego * age
      if d_rel <= 0.0:
        continue
      out.append({**det, "dRel": d_rel, "bearing": math.atan2(-det["yRel"], d_rel)})
    return out

  def detect(self, extrinsics_msg, extrinsics_valid: bool, now: float, camera_to_front: float) -> list[dict]:
    """Camera -> YOLO -> car-frame projections; ``[]`` keeps the frame radar-only.

    Only called on ``vision_due`` ticks (see :meth:`process`): the enable/disable
    gating lives in :meth:`process`, so every call here either runs the full
    chain or hits one of the degrade paths below.
    """
    geom = calibrated_geometry_from_msg(extrinsics_msg, valid=extrinsics_valid)
    if geom is None:
      # 0.5deg pitch error = 41% distance error at 40m. Running the vision path
      # on an uncalibrated camera is exactly how spurious biases get produced,
      # so fall back to radar-only until openpilot's calibration converges.
      self._degrade("calibration")
      self._detect_state = "calibrating"
      return []
    if self.camera is None:
      self.camera = self.camera_factory()
    # Intrinsics are only known after the first successful connect; until then
    # the ROI falls back to the frame centre (horizon_row=None).
    intrinsics = self.camera.intrinsics
    horizon_row = horizon_row_for(intrinsics[3], intrinsics[1], geom) if intrinsics is not None else None
    frame = self.camera.frame(horizon_row=horizon_row)
    if frame is None:
      # No camerad stream (PC) or no fresh frame this tick; connect keeps
      # retrying inside CameraStream, the reason is only logged once. Zero the
      # duration too: a stale one would keep the processing_cost throttle reason
      # alive for interval decisions that saw no inference at all.
      self.last_vision_duration_s = 0.0
      self._degrade("camera")
      self._detect_state = "noCamera"
      return []
    roi, roi_meta = frame
    if self.detector is None:
      self.detector = YoloDetector(YOLO_PKL_PATH)
    if self.detector_dead:
      self._detect_state = "noModel"
      return []
    try:
      t0 = time.perf_counter()
      detections = self.detector.infer(roi, now=now)
      self.last_vision_duration_s = time.perf_counter() - t0
    except Exception:
      # Missing pkl (weights are not in the repo) or a hard inference failure:
      # radar-only from here on, logged once, never crash the daemon.
      self.detector_dead = True
      self._degrade("yolo")
      self._detect_state = "noModel"
      cloudlog.exception("eagled: YOLO inference failed")
      return []
    self._detect_state = "ok"
    fx, fy, cx, cy = self.camera.intrinsics
    return project_detections(detections, fx=fx, fy=fy, cx=cx, cy=cy,
                              height=C.CAMERA_HEIGHT, pitch=geom.pitch,
                              yaw=geom.yaw, roll=geom.roll,
                              camera_to_front=camera_to_front, roi_meta=roi_meta,
                              frame_height=self.camera.frame_size[1] if self.camera.frame_size else None)

  def process(self, sm, now: float, v_ego: float, camera_to_front: float,
              vision_enabled: bool = True, vision_due: bool = True) -> PerceptionFrame:
    """Run the full fusion chain once and return the frame for this tick.

    两个开关分开:``vision_enabled`` 是视觉链总开关(AvoidanceEnabled),
    ``vision_due`` 是"本拍该不该推理"(健康门控的节拍)。非 due 帧不推理,
    但沿用 TTL 内的上次检测(按 ego 位移补偿)—— 否则 eagleDebug 的视觉
    目标在推理间隙被纯雷达帧清空,lanlink 上车/人/自行车闪烁消失。
    """
    radar = sm['radarTracks']
    model_v2 = sm['modelV2']
    self._absorb_worker()
    self.vision_state = self._detect_state if vision_enabled else "off"
    if not vision_enabled:
      # 避让关:立即清空保持,回到纯雷达(不让旧目标活过 TTL)。代际 +1 作废
      # 在途推理——它完成时不得复活旧目标。
      self._hold_gen += 1
      self._store_hold([], now)
      self.last_vision_duration_s = 0.0
      detections = []
    elif vision_due:
      if self._worker is not None:
        # 异步:本拍沿用旧 hold(补偿后),结果稍后由 _absorb_worker 落库
        self._submit_vision(sm, now, camera_to_front)
        detections = self._held_detections(now, v_ego)
      else:
        # due 帧的结果(含降级路径的空列表)整体成为新的保持:保持桥接的是
        # 调度间隙,不是视觉故障 —— 降级帧不该让上一拍的旧目标复活。
        detections = self.detect(sm['extrinsicsCalibration'], sm.valid['extrinsicsCalibration'], now, camera_to_front)
        self._store_hold(detections, now)
    else:
      detections = self._held_detections(now, v_ego)
    # associate needs fy for the box-height fallback on unmatched detections.
    # Intrinsics live on the camera (None until the first successful connect),
    # and with no detections associate never reads fy, so 0.0 is a safe fallback.
    fy = self.camera.intrinsics[1] if self.camera is not None and self.camera.intrinsics else 0.0
    n_associated, fused, pairs = associate(radar.points, detections, fy=fy, camera_to_front=camera_to_front)
    # Confirmation keys are trackId-based (radar_point_key): pycapnp hands out a
    # fresh wrapper object on every access to radar.points, so id() keys built
    # from associate's materialized points would never match the points
    # fuse_objects iterates here.
    confirmed_keys = tuple(radar_point_key(p[0]) for p in pairs)
    vision_cls_by_key = {radar_point_key(p[0]): p[3] for p in pairs}
    # C2/C7: 车道几何一次提取,本帧所有目标判定共用（含遥测侧的 _target_rows）。
    lane_geo = lane_geometry(model_v2, camera_to_front)
    objects = fuse_objects(radar.points, fused, v_ego=v_ego,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key)
    return PerceptionFrame(radar_points=list(radar.points), detections=detections,
                           n_associated=n_associated, pairs=pairs,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key,
                           objects=objects, lane_geo=lane_geo)
