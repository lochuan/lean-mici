"""Tests for radar <-> vision association on bearing (core shared with the shadow harness)."""

import math

import pytest

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.association import associate, nearest_pairs_by_bearing
from openpilot.selfdrive.eagled.avoidance_planner import _in_gate, fuse_targets
from openpilot.selfdrive.eagled.projection import project_detections

FY = 425.25
FX, CX, CY = 425.25, 672.0, 380.0


class R:
  def __init__(self, d, y, v=0.0):
    self.dRel, self.yRel, self.vRel = d, y, v


def _det(bearing, cls="person", h_px=40.0, conf=0.6):
  return {"bearing": bearing, "cls": cls, "boxHeightPx": h_px, "conf": conf}


class VO:
  """Object-style vision side: shadow.py passes VisionObject(x, y), not dicts."""

  def __init__(self, x, y, cls=None, box_height_px=None):
    self.x, self.y = x, y
    self.cls = cls
    self.boxHeightPx = box_height_px


def test_matching_is_mutually_exclusive():
  """1 个视觉目标 + 2 个同方位雷达点 -> 只能配一次。

  框高测距换算到保险杠系后 ≈20.0m, 两个雷达点(20.0/20.5m)都在二级距离门内,
  都是活候选 —— n==1 只能由互斥保证。去掉 used_v(或两个守卫都去)此测试必红;
  只去掉 used_r 它仍然绿(第二个候选的视觉侧已被占用)—— used_r 方向由
  test_radar_exclusion_two_detections_cannot_share_one_radar 钉住。
  """
  h_px = FY * C.CLASS_HEIGHTS_M["person"] / (20.0 + C.CAMERA_TO_FRONT)  # 保险杠系 20.0m
  radars = [R(20.0, 0.5), R(20.5, 0.6)]
  dets = [_det(math.atan2(-0.55, 20.2), h_px=h_px)]
  n, fused, pairs = associate(radars, dets, fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 1
  assert len(pairs) == 1
  assert fused == []


def test_radar_exclusion_two_detections_cannot_share_one_radar():
  """2 检测 + 2 雷达,两个检测都只够得着 r0(r1 在方位门外)。

  used_r 守卫单独负责这里:去掉它 d0、d1 会各自认领同一个 r0(n==2);守卫在位
  时 d1 落空、退回框高测距(n==1)。与上个测试互补 —— 那个只钉 used_v。"""
  h_px = FY * C.CLASS_HEIGHTS_M["person"] / (20.0 + C.CAMERA_TO_FRONT)  # 保险杠系 20.0m
  radars = [R(20.0, 0.0), R(20.0, 1.5)]   # r1 方位角 -0.0749,离两个检测都 > 0.035
  dets = [_det(0.0, h_px=h_px), _det(0.01, h_px=h_px)]
  n, fused, pairs = associate(radars, dets, fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 1
  assert len(pairs) == 1
  assert pairs[0][0] is radars[0]
  assert pairs[0][1] is dets[0]
  assert len(fused) == 1                  # d1 未匹配 -> 退回框高测距


def test_unmatched_detection_gets_box_height_range():
  dets = [_det(0.0, cls="person", h_px=FY * 1.7 / 25.0)]
  n, fused, pairs = associate([], dets, fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 0 and pairs == []
  assert len(fused) == 1
  # 25.0 是相机系; dRel 与雷达同参照(保险杠系), 必须减安装偏置。
  assert fused[0]["dRel"] == pytest.approx(25.0 - C.CAMERA_TO_FRONT, rel=1e-3)
  assert fused[0]["dRelSource"] == "boxheight"


def test_unmatched_detection_yrel_sign_follows_bearing():
  """bearing 向图像右为正, 而 yRel 左正 -> 必须反号。"""
  right = associate([], [_det(0.1, h_px=FY * 1.7 / 20.0)], fy=FY, camera_to_front=C.CAMERA_TO_FRONT)[1][0]
  assert right["yRel"] < 0.0


def test_detection_without_usable_height_is_dropped():
  assert associate([], [_det(0.0, cls="unknown", h_px=40.0)], fy=FY, camera_to_front=C.CAMERA_TO_FRONT)[1] == []


def test_bearing_gate_rejects_a_far_off_target():
  radars = [R(20.0, 0.0)]
  dets = [_det(C.ASSOC_MAX_DBEARING * 3)]
  n, fused, pairs = associate(radars, dets, fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 0
  assert len(fused) == 1          # 未匹配 -> 退回框高测距


def test_range_gate_rejects_same_bearing_different_range():
  """同方位但距离差极大的目标不应配上(方位角单独不足以判别)。"""
  radars = [R(40.0, 0.0)]
  dets = [_det(0.0, cls="person", h_px=FY * 1.7 / 5.0)]   # 框高说相机系 5m
  n, fused, pairs = associate(radars, dets, fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 0


def test_matched_pair_carries_the_vision_class():
  """匹配对要用视觉类别升级权重: 雷达看到的摩托车不该按 vehicle 算。"""
  radars = [R(20.0, 0.0)]
  dets = [_det(0.0, cls="motorcycle", h_px=FY * 1.7 / 20.0)]
  n, fused, pairs = associate(radars, dets, fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 1
  assert pairs[0][3] == "motorcycle"


# --- object-style (non-dict) vision input -------------------------------------

def test_object_style_vision_matches_via_derived_bearing():
  """shadow.py 传的是 VisionObject(x, y) 冻结数据类: 方位角必须能从对象自身的
  度量坐标推出(atan2(-y, x)), 不能走 dict 下标。旧实现用 _object_xy 兼容两种
  形态, 重写时丢掉了这个双态支持 —— 这里钉死它不能再次悄悄退化。"""
  radar = [R(20.0, -1.0)]
  obj = VO(x=20.0, y=-1.0)   # bearing = atan2(1.0, 20.0) == radar bearing
  n, fused, pairs = associate(radar, [obj], fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 1
  assert pairs[0][1] is obj
  assert pairs[0][3] is None  # 对象侧没有类别


def test_object_style_secondary_gate_uses_object_x():
  """无 cls/boxHeightPx 的对象: 二级门退用对象自带的 x 作为它的距离。"""
  radar = [R(40.0, 0.0)]
  pairs, _ = nearest_pairs_by_bearing(radar, [VO(x=5.0, y=0.0)], FY,
                                      C.ASSOC_MAX_DBEARING, C.ASSOC_MAX_DRANGE_M, camera_to_front=C.CAMERA_TO_FRONT)
  assert pairs == []          # 同方位, 但对象自报 5m vs 雷达 40m


def test_object_style_box_height_attrs_drive_the_gate():
  """对象侧带 cls/boxHeightPx 属性时, 二级门优先用框高测距。"""
  radar = [R(40.0, 0.0)]
  obj = VO(x=40.0, y=0.0, cls="person", box_height_px=FY * 1.7 / 5.0)  # 框高说 5m
  pairs, _ = nearest_pairs_by_bearing(radar, [obj], FY,
                                      C.ASSOC_MAX_DBEARING, C.ASSOC_MAX_DRANGE_M, camera_to_front=C.CAMERA_TO_FRONT)
  assert pairs == []


def test_dict_without_gate_inputs_skips_secondary_gate():
  """只有 bearing 的 dict: 无 cls/boxHeightPx 也不暴露 x -> 跳过二级门, 纯方位匹配。"""
  radar = [R(40.0, 0.0)]
  n, fused, pairs = associate(radar, [{"bearing": 0.0}], fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 1
  assert fused == []


def test_nearest_pairs_by_bearing_pair_shape():
  radars = [R(20.0, 0.0)]
  dets = [_det(0.0, cls="motorcycle", h_px=FY * 1.7 / 20.0)]
  pairs, matched = nearest_pairs_by_bearing(radars, dets, FY, C.ASSOC_MAX_DBEARING, C.ASSOC_MAX_DRANGE_M, camera_to_front=C.CAMERA_TO_FRONT)
  assert matched == {0}
  radar, obj, db, cls = pairs[0]
  assert radar is radars[0] and obj is dets[0]
  assert db == pytest.approx(0.0, abs=1e-12)
  assert cls == "motorcycle"


# --- projection -> association: bearing 必须是保险杠原点 ------------------------
# 相机装在保险杠后方 CAMERA_TO_FRONT 处。像素列方位角(atan2(u-cx, fx))绕的是
# **相机**光心,而雷达方位角(atan2(-yRel, dRel))绕的是**保险杠**原点。混用两个
# 原点有两个独立后果:未匹配检测的 yRel 被缩小 d/(d+CAMERA_TO_FRONT) 倍(5m 处
# -23%),近距 VRU 掉进 own-lane 门;真实配对背上 ~0.037 rad 的假 Δbearing
# (8m/横向 2m 就超 0.035 门限)。投影 dict 的 bearing 因此必须是车体系
# atan2(-yRel, dRel),与 _radar_bearing 同一参照。

def _box_at(d_rel, y_rel, cls="person", conf=0.9):
  """全帧框:底中心反投影到保险杠系 (d_rel, y_rel),框高使框高测距也返回 d_rel
  (保险杠系)—— 与 test_daemon_fusion / test_shadow 的同名 helper 同一约定。"""
  d_cam = d_rel + C.CAMERA_TO_FRONT
  v = CY + FY * C.CAMERA_HEIGHT / d_cam
  u = CX - FX * y_rel / d_cam
  h_px = FY * C.CLASS_HEIGHTS_M[cls] / d_cam
  return {"x1": u - 10.0, "y1": v - h_px, "x2": u + 10.0, "y2": v, "cls": cls, "conf": conf}


def _project(box):
  return project_detections([box], fx=FX, fy=FY, cx=CX, cy=CY, height=C.CAMERA_HEIGHT,
                            camera_to_front=C.CAMERA_TO_FRONT)


def test_vision_only_close_vru_survives_the_own_lane_gate():
  """5m、横向 1.5m 的 vision-only 行人必须落在 own-lane 门(1.2m)之外。

  相机原点方位角配保险杠系距离会把 yRel 缩小 d/(d+CAMERA_TO_FRONT) 倍:
  1.5m 被缩到 ~1.15m,掉进 own-lane 门,避让整条丢失。"""
  n, fused, pairs = associate([], _project(_box_at(5.0, 1.5)), fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 0 and pairs == []
  assert len(fused) == 1
  assert fused[0]["yRel"] == pytest.approx(1.5, rel=1e-3)
  assert fused[0]["dRel"] == pytest.approx(5.0, rel=1e-3)
  targets = fuse_targets([], fused, v_ego=20.0)
  assert len(targets) == 1              # own-lane 门不得吞掉它
  assert _in_gate(targets[0].dRel, targets[0].yRel)


def test_short_range_large_offset_pair_matches():
  """8m、横向 2.0m 的真实雷达/视觉对必须配上。

  相机原点 vs 保险杠原点的视差在这个几何下贡献 ~0.037 rad 假 Δbearing,
  超过 0.035 门限 —— 真配对被拆散。"""
  radars = [R(8.0, 2.0)]
  n, fused, pairs = associate(radars, _project(_box_at(8.0, 2.0)), fy=FY, camera_to_front=C.CAMERA_TO_FRONT)
  assert n == 1
  assert fused == []
  assert pairs[0][3] == "person"
