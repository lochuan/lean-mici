"""eagled perception core: the fusion chain behind the eagle.

Turns raw sensors into the per-frame target picture: wide-road camera frame
-> YOLO ROI inference -> ground-plane projection into the car frame -> radar
association -> ``fuse_targets``. No cereal, no params, no decisions here —
``EagleDaemon`` drives :class:`PerceptionCore` once per 5Hz tick and
publishes what it produces (``eagleState`` for consumers, ``eagleDebug``
for the lanlink UI and calibration); the avoidance planner is simply the
first in-process consumer of :class:`PerceptionFrame`. The planned second
consumer (desire_helper in modeld, lane-change gating) reads the published
``eagleState`` instead.

Module taxonomy: fusion primitives (``RadarPoint``/``Target``/``_sign``/``_in_gate``/
``radar_point_key``/``fuse_targets``/``lane_geometry``/``gate_target``) live
HERE. ``avoidance_planner`` re-exports them for import compatibility and keeps
the decision layer (``plan``/``AvoidancePlanner``) on top of them.

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

import numpy as np

from openpilot.common.model_geometry import geometry_to_vehicle_frame
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.association import associate
from openpilot.selfdrive.eagled.camera_stream import CameraStream
from openpilot.selfdrive.eagled.projection import geometry_from_calibration, horizon_row_for, project_detections
from openpilot.selfdrive.eagled.yolo_detector import YoloDetector

YOLO_PKL_PATH = Path(__file__).parent / "models" / "yolo_tinygrad.pkl"

# 两次推理之间沿用上次检测的最长时间。推理节拍由健康门控拉长(0.2s~1.5s),
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
  side: int    # -1 = right of ego (yRel < 0), +1 = left
  dRel: float  # m, longitudinal distance
  yRel: float  # m, lateral position, left positive
  w: float     # class weight (VRU > vehicle)
  conf: float  # detector confidence [0, 1]
  lane: int = 0     # -1 左邻 / 0 本道或重叠 / +1 右邻（分类未参与时 0）
  in_gate: bool = True  # fuse_objects 已按三级门控裁决；planner 不再重算几何门
  cls: str | None = None  # 视觉类别（car/person/...；纯雷达未关联为 None）—— 半宽折算用
  vRel: float | None = None  # 纵向相对速度 m/s（雷达实测;视觉独有目标速度未知 = None,
                             # 变道时间投影对 None 不放宽）


@dataclass(frozen=True)
class LaneGeometry:
  """Ego-lane boundaries + model path in the **vehicle frame** (y left-positive).

  几何来自 ``model_geometry`` 的换算输出：x 以前保险杠为原点（dRel 同参照）、
  y 左正（yRel 同参照）——判定里目标与几何同原点，无手写坐标换算。

  ``left_valid``/``right_valid`` 是 C7 置信门（laneLineProbs/Stds）的逐边裁决：
  边界线磨损/无划线的那一侧为 False，该侧目标回退到 path-relative 层。
  """
  left_valid: bool
  right_valid: bool
  left_x: tuple
  left_y: tuple
  right_x: tuple
  right_y: tuple
  path_x: tuple
  path_y: tuple
  path_std: tuple


def lane_geometry(model_v2, camera_to_front: float) -> LaneGeometry | None:
  """Extract ego-lane boundaries (laneLines[1]/[2]) + path with C7 quality flags.

  几何本身经 ``model_geometry`` 换算（解释权的唯一落点）：输出已是车体系
  （x 前保险杠原点、y 左正），目标与几何在判定中同原点。``camera_to_front``
  为安装偏移（票 #6 读点注入，本模块不持 Params）。

  None = 模型几何整体不可用（字段缺失/列表为空）——目标判定回退固定带。
  对 duck-typed 测试桩安全：缺属性的假 modelV2 直接得 None。
  """
  probs = getattr(model_v2, "laneLineProbs", None)
  stds = getattr(model_v2, "laneLineStds", None)
  position = getattr(model_v2, "position", None)
  if probs is None or stds is None or position is None:
    return None
  if len(probs) <= C.LANE_IDX_RIGHT or len(stds) <= C.LANE_IDX_RIGHT:
    return None
  vgeo = geometry_to_vehicle_frame(model_v2, camera_to_front=camera_to_front)
  left, right = vgeo.lane_lines[C.LANE_IDX_LEFT], vgeo.lane_lines[C.LANE_IDX_RIGHT]
  if left is None or right is None:
    return None
  left_valid = float(probs[C.LANE_IDX_LEFT]) >= C.LANE_PROB_MIN and float(stds[C.LANE_IDX_LEFT]) <= C.LANE_STD_MAX
  right_valid = float(probs[C.LANE_IDX_RIGHT]) >= C.LANE_PROB_MIN and float(stds[C.LANE_IDX_RIGHT]) <= C.LANE_STD_MAX
  path = vgeo.path
  path_x = path.x if path is not None else ()
  # duck-typed position 桩可能没有 yStd -> tier 2 安全关闭
  path_std = tuple(getattr(position, "yStd", ()) or ())
  return LaneGeometry(
    left_valid=left_valid, right_valid=right_valid,
    left_x=left.x, left_y=left.y,
    right_x=right.x, right_y=right.y,
    path_x=path_x, path_y=(path.y if path is not None else ()),
    path_std=path_std if len(path_std) == len(path_x) else (),
  )


def gate_target(dRel: float, yRel: float, cls, geo: LaneGeometry | None) -> tuple[bool, int]:
  """Threat decision + lane label for one candidate. 单一事实来源:
  ``fuse_targets``（planner 输入）和 ``_target_rows``（遥测）都走这里,
  遥测与行动不可能不一致。

  三级判定（C2 车道相对 + C7 置信门）:
  1. **lane-relative** — 本道该侧边界线可信（probs/stds 过门）: 车身边缘
     （中心 ± 类别半宽）越过车道线 = 威胁。"车屁股侵入车道"的语义来源。
  2. **path-relative** — 边界不可信、但 position.yStd 在目标距离处达标:
     相对模型路径的固定带（弯道上仍正确——同车道目标 ≈ 0 偏差被排除）。
  3. **fixed band** — 模型几何全不可信: 相对车体的固定 yRel 带（旧行为）。

  tier 1/2/3 都要求中心已偏出 ``OWN_LANE_HALF_WIDTH``: 0.35m 偏置绕不开
  本道中心的障碍,那留给纵向和驾驶员。

  Frame: 全链路车体系（yRel 左正、x 前保险杠原点）——geo 来自
  ``model_geometry`` 的换算输出,目标与几何同原点,本函数不做坐标换算。
  """
  if not (0.0 < dRel <= C.D_GATE):
    return False, 0
  if geo is not None:
    if yRel > 0.0 and geo.left_valid:
      # 目标在左:车身边缘(yRel - hw)越过左边界即侵入本道
      bound = float(np.interp(dRel, geo.left_x, geo.left_y))
      intrudes = (yRel - C.class_half_width(cls)) < bound
      lane = -1 if yRel > bound else 0
      return intrudes and abs(yRel) >= C.OWN_LANE_HALF_WIDTH, lane
    if yRel < 0.0 and geo.right_valid:
      bound = float(np.interp(dRel, geo.right_x, geo.right_y))
      intrudes = (yRel + C.class_half_width(cls)) > bound
      lane = 1 if yRel < bound else 0
      return intrudes and abs(yRel) >= C.OWN_LANE_HALF_WIDTH, lane
    # tier 2: path-relative（边界该侧不可信时的弯道正确回退）
    if len(geo.path_x) > 0 and len(geo.path_std) == len(geo.path_x):
      if float(np.interp(dRel, geo.path_x, geo.path_std)) <= C.PATH_STD_MAX:
        # 路径横向偏差,左正语义直接相减
        d_y_rel = yRel - float(np.interp(dRel, geo.path_x, geo.path_y))
        off_path = C.OWN_LANE_HALF_WIDTH <= abs(d_y_rel) <= C.Y_GATE
        lane = (-1 if d_y_rel > 0 else 1) if abs(d_y_rel) >= C.OWN_LANE_HALF_WIDTH else 0
        return off_path, lane
  # tier 3: fixed band（旧行为,模型几何全不可信时的兜底）
  in_band = C.OWN_LANE_HALF_WIDTH <= abs(yRel) <= C.Y_GATE
  return in_band, (-1 if yRel > 0 else 1) if in_band else 0


def _sign(value: float) -> int:
  return 1 if value >= 0.0 else -1


def _in_gate(dRel: float, yRel: float) -> bool:
  """Historical fixed-band predicate — gate_target 的 tier 3 语义定义。

  生产路径不再单独调用它（tier 3 内联在 gate_target 里,带回退的 lane 标签）,
  但测试用它构造带内/带外 fixtures,它是固定带行为的可执行文档。
  """
  return 0.0 < dRel <= C.D_GATE and C.OWN_LANE_HALF_WIDTH <= abs(yRel) <= C.Y_GATE


def radar_point_key(point) -> int:
  """Stable identity key for a radar point across re-iteration.

  pycapnp constructs a fresh wrapper object on every access to
  ``radarTracks.points``, so ``id()`` differs between the pass that built the
  association pairs and any later pass over the same message -- on device an
  ``id()`` key never matches. ``trackId`` is the identity-stable key (required
  UInt64, no reuse, per car.capnp); ``id()`` remains only as the fallback for
  duck-typed fixtures that carry no ``trackId``. All confirmation-key building
  (fuse_targets and every call site) goes through this helper.
  """
  track_id = getattr(point, "trackId", None)
  return id(point) if track_id is None else int(track_id)


def fuse_objects(radar_points: Iterable[RadarPoint], detections: Iterable[dict] | None = None,
                 v_ego: float = 0.0, confirmed_keys: Iterable[int] = (),
                 vision_cls_by_key: dict[int, str] | None = None,
                 lane_geo: LaneGeometry | None = None) -> list[Target]:
  """ALL fused objects (in-gate or not), each carrying its gate_target verdict.

  Same validity rules as the historical fuse_targets: static radar points
  without vision confirmation are dropped entirely (a phantom guardrail must
  not constrain the budget either), confirmed points take the vision class
  weight. ``Target.in_gate`` carries the threat verdict; ``Target.lane`` the
  lane label. The side-budget pass (C9) consumes this full list — adjacent-lane
  traffic is by definition OUT of the threat gate, yet it is exactly what
  constrains lateral movement.
  """
  confirmed = set(confirmed_keys)
  cls_by_key = vision_cls_by_key or {}
  objects: list[Target] = []
  for point in radar_points:
    dRel, yRel = float(point.dRel), float(point.yRel)
    key = radar_point_key(point)
    # vRel 缺失时保守按静止处理
    v_rel = getattr(point, "vRel", None)
    ground_speed = None if v_rel is None else abs(float(v_rel) + v_ego)
    if (ground_speed is None or ground_speed < C.STATIC_SPEED_THRESH) and key not in confirmed:
      continue
    cls = cls_by_key.get(key)
    in_gate, lane = gate_target(dRel, yRel, cls, lane_geo)
    objects.append(Target(side=_sign(yRel), dRel=dRel, yRel=yRel, w=C.class_weight(cls), conf=1.0,
                          lane=lane, in_gate=in_gate, cls=cls,
                          vRel=float(v_rel) if v_rel is not None else None))
  for det in detections or []:
    try:
      dRel, yRel = float(det["dRel"]), float(det["yRel"])
    except (KeyError, TypeError, ValueError):
      continue
    cls = det.get("cls")
    in_gate, lane = gate_target(dRel, yRel, cls, lane_geo)
    # 视觉独有目标(雷达没关联上)没有纵向速度:None,变道投影不放宽
    objects.append(Target(side=_sign(yRel), dRel=dRel, yRel=yRel, w=C.class_weight(cls),
                          conf=float(det.get("conf", 1.0)), lane=lane, in_gate=in_gate,
                          cls=cls, vRel=None))
  return objects


def fuse_targets(radar_points: Iterable[RadarPoint], detections: Iterable[dict] | None = None,
                 v_ego: float = 0.0, confirmed_keys: Iterable[int] = (),
                 vision_cls_by_key: dict[int, str] | None = None,
                 lane_geo: LaneGeometry | None = None) -> list[Target]:
  """Build planner targets: the in-gate threats, exactly as before.

  Thin filter over :func:`fuse_objects`' full object list (backward-compatible
  facade): ``confirmed_keys``/``vision_cls_by_key`` semantics, static-confirmation
  gate and vision class weights are documented there.
  """
  return [t for t in fuse_objects(radar_points, detections, v_ego, confirmed_keys,
                                  vision_cls_by_key, lane_geo) if t.in_gate]


@dataclass(frozen=True)
class SidePicture:
  """C9:一侧的横向态势 —— 预算 + 最紧约束目标 + 变道清空。

  ``budget`` 是连续量(m):BSM 报警 -> 0;侧向目标存在 -> 按最紧目标的
  近缘间隙折算;无约束 -> BUDGET_UNCONSTRAINED。``lead`` 是折算出该预算的
  目标(BSM 强制 0 时为 None —— 布尔报警没有可指向的目标)。
  ``change_clear`` 是变道门语义(desire_helper 消费):该侧目标道窗内全部
  目标过近区/速度/时间投影三关 + BSM 静默。远而快的侧车放行。
  """
  budget: float
  lead: Target | None
  change_clear: bool = True


def side_pictures(objects: Iterable[Target], bsm_left: bool, bsm_right: bool,
                  v_ego: float = 0.0) -> tuple[SidePicture, SidePicture]:
  """每侧横向态势(左,右):预算 + 最紧约束目标 + 变道清空判定。单一事实来源:
  planner 的偏置上限、eagleState 的发布值、desire_helper 的变道门同源,
  shadow 复放与在线行为不可能不一致。

  **预算**(避让消费):偏置 b 向该侧后,要求与该侧最近目标的**车身间隙**
  仍 ≥ SIDE_MARGIN:
    gap = (|yRel| - 目标半宽) - EGO_HALF_WIDTH;  budget = gap - SIDE_MARGIN
  取该侧全部满足纵向窗口/横向范围目标的最小值。窗外(|yRel| > SIDE_MAX_Y 或
  dRel > SIDE_WINDOW_D)的物体两个车道开外,不参与。

  **变道清空**(desire_helper 消费,carrotpilot 时间投影语义):目标道窗内
  目标全部满足才清空。目标不清空当且仅当:
    - 近区硬拦: dRel ≤ LANE_CHANGE_NEAR_D(贴身车,BSM 覆盖不到的前角);
    - 速度未知: vRel is None(视觉独有目标,雷达没测到速度,不放宽);
    - 时间投影: 侧车 LANE_CHANGE_LEAD_TIME_S 秒后位置 ≤ 我们
      LANE_CHANGE_EGO_TIME_S 秒后位置(对方少跑 1 秒的裕量,同 carrotpilot
      4s/3s)。"远而快"的侧车因此放行;对向车(vLead < 0)投影急剧收缩,
      天然被拦 —— C4 对向场景的伏笔。
  BSM 报警侧直接不清空(后侧盲区由它守)。
  """
  side_objs: dict[int, list[Target]] = {1: [], -1: []}
  best: dict[int, tuple[float, Target]] = {}
  for obj in objects:
    if not (0.0 < obj.dRel <= C.SIDE_WINDOW_D) or abs(obj.yRel) > C.SIDE_MAX_Y:
      continue
    edge_dist = abs(obj.yRel) - C.class_half_width(obj.cls)
    budget = edge_dist - C.EGO_HALF_WIDTH - C.SIDE_MARGIN
    side = 1 if obj.yRel > 0.0 else -1
    side_objs[side].append(obj)
    if side not in best or budget < best[side][0]:
      best[side] = (budget, obj)

  def _picture(bsm: bool, side: int, objs: list[Target]) -> SidePicture:
    if bsm:
      return SidePicture(budget=0.0, lead=None, change_clear=False)
    budget, lead = (max(0.0, best[side][0]), best[side][1]) if side in best \
      else (C.BUDGET_UNCONSTRAINED, None)
    clear = True
    for obj in objs:
      if obj.dRel <= C.LANE_CHANGE_NEAR_D or obj.vRel is None:
        clear = False
        break
      v_lead = obj.vRel + v_ego
      if obj.dRel + v_lead * C.LANE_CHANGE_LEAD_TIME_S <= v_ego * C.LANE_CHANGE_EGO_TIME_S:
        clear = False
        break
    return SidePicture(budget=budget, lead=lead, change_clear=clear)

  left = _picture(bsm_left, 1, side_objs[1])
  right = _picture(bsm_right, -1, side_objs[-1])
  return left, right


@dataclass
class PerceptionFrame:
  """One 5Hz fusion output: everything the publishers and the planner need."""
  radar_points: list          # materialized list of this tick's radar points
  detections: list[dict]      # car-frame projected YOLO detections
  n_associated: int
  pairs: list                 # (radar_point, det, pair_id, cls) from associate()
  confirmed_keys: tuple
  vision_cls_by_key: dict
  targets: list[Target]       # planner-ready in-gate threats
  objects: list[Target] = None  # full fused object list, in-gate or not (C9 预算输入)
  lane_geo: LaneGeometry | None = None  # C2/C7 geometry + quality flags (None = 模型几何不可用)
  left: SidePicture = None    # C9: 左侧预算 + 最紧约束目标
  right: SidePicture = None   # C9: 右侧预算 + 最紧约束目标

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
    """TTL 内沿用上次成功推理的检测,dRel 按自车速度补偿。

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
    geom = geometry_from_calibration(extrinsics_msg, extrinsics_valid)
    if not geom.valid:
      # 0.5deg pitch error = 41% distance error at 40m. Running the vision path
      # on an uncalibrated camera is exactly how spurious biases get produced,
      # so fall back to radar-only until openpilot's calibration converges.
      self._degrade("calibration")
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
      return []
    roi, roi_meta = frame
    if self.detector is None:
      self.detector = YoloDetector(YOLO_PKL_PATH)
    if self.detector_dead:
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
      cloudlog.exception("eagled: YOLO inference failed")
      return []
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
    # fuse_targets iterates here.
    confirmed_keys = tuple(radar_point_key(p[0]) for p in pairs)
    vision_cls_by_key = {radar_point_key(p[0]): p[3] for p in pairs}
    # C2/C7: 车道几何一次提取,本帧所有目标判定共用（含遥测侧的 _target_rows）。
    lane_geo = lane_geometry(model_v2, camera_to_front)
    objects = fuse_objects(radar.points, fused, v_ego=v_ego,
                          confirmed_keys=confirmed_keys,
                          vision_cls_by_key=vision_cls_by_key,
                          lane_geo=lane_geo)
    # C9: 预算从全量 objects 折算（邻道正常车流在威胁门之外,恰是约束横向移动的东西）。
    car_state = sm['carState']
    left, right = side_pictures(objects, bool(car_state.leftBlindspot), bool(car_state.rightBlindspot),
                                v_ego=float(car_state.vEgo))
    return PerceptionFrame(radar_points=list(radar.points), detections=detections,
                           n_associated=n_associated, pairs=pairs,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key,
                           targets=[t for t in objects if t.in_gate],
                           objects=objects, lane_geo=lane_geo, left=left, right=right)
