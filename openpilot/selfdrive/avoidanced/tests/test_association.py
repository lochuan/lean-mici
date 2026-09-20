"""Tests for radar <-> vision association on bearing (core shared with the shadow harness)."""

import math

import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.association import associate, nearest_pairs_by_bearing

FY = 425.25


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

  旧实现每个雷达点各自挑最近视觉目标且不互斥,n_associated 会超过检测总数,
  既虚高关联率,又让行人被并入 VEHICLE_WEIGHT 的雷达点而丢掉 VRU 权重。
  """
  radars = [R(20.0, 0.5), R(20.5, 0.6)]
  dets = [_det(math.atan2(-0.55, 20.2))]
  n, fused, pairs = associate(radars, dets, fy=FY)
  assert n == 1
  assert len(pairs) == 1
  assert fused == []


def test_unmatched_detection_gets_box_height_range():
  dets = [_det(0.0, cls="person", h_px=FY * 1.7 / 25.0)]
  n, fused, pairs = associate([], dets, fy=FY)
  assert n == 0 and pairs == []
  assert len(fused) == 1
  assert fused[0]["dRel"] == pytest.approx(25.0, rel=1e-3)
  assert fused[0]["dRelSource"] == "boxheight"


def test_unmatched_detection_yrel_sign_follows_bearing():
  """bearing 向图像右为正, 而 yRel 左正 -> 必须反号。"""
  right = associate([], [_det(0.1, h_px=FY * 1.7 / 20.0)], fy=FY)[1][0]
  assert right["yRel"] < 0.0


def test_detection_without_usable_height_is_dropped():
  assert associate([], [_det(0.0, cls="unknown", h_px=40.0)], fy=FY)[1] == []


def test_bearing_gate_rejects_a_far_off_target():
  radars = [R(20.0, 0.0)]
  dets = [_det(C.ASSOC_MAX_DBEARING * 3)]
  n, fused, pairs = associate(radars, dets, fy=FY)
  assert n == 0
  assert len(fused) == 1          # 未匹配 -> 退回框高测距


def test_lateral_secondary_gate_rejects_same_bearing_different_range():
  """同方位但距离差极大的目标不应配上(方位角单独不足以判别)。"""
  radars = [R(40.0, 0.0)]
  dets = [_det(0.0, cls="person", h_px=FY * 1.7 / 5.0)]   # 框高说 5m
  n, fused, pairs = associate(radars, dets, fy=FY)
  assert n == 0


def test_matched_pair_carries_the_vision_class():
  """匹配对要用视觉类别升级权重: 雷达看到的摩托车不该按 vehicle 算。"""
  radars = [R(20.0, 0.0)]
  dets = [_det(0.0, cls="motorcycle", h_px=FY * 1.7 / 20.0)]
  n, fused, pairs = associate(radars, dets, fy=FY)
  assert n == 1
  assert pairs[0][3] == "motorcycle"


# --- object-style (non-dict) vision input -------------------------------------

def test_object_style_vision_matches_via_derived_bearing():
  """shadow.py 传的是 VisionObject(x, y) 冻结数据类: 方位角必须能从对象自身的
  度量坐标推出(atan2(-y, x)), 不能走 dict 下标。旧实现用 _object_xy 兼容两种
  形态, 重写时丢掉了这个双态支持 —— 这里钉死它不能再次悄悄退化。"""
  radar = [R(20.0, -1.0)]
  obj = VO(x=20.0, y=-1.0)   # bearing = atan2(1.0, 20.0) == radar bearing
  n, fused, pairs = associate(radar, [obj], fy=FY)
  assert n == 1
  assert pairs[0][1] is obj
  assert pairs[0][3] is None  # 对象侧没有类别


def test_object_style_secondary_gate_uses_object_x():
  """无 cls/boxHeightPx 的对象: 二级门退用对象自带的 x 作为它的距离。"""
  radar = [R(40.0, 0.0)]
  pairs, _ = nearest_pairs_by_bearing(radar, [VO(x=5.0, y=0.0)], FY,
                                      C.ASSOC_MAX_DBEARING, C.ASSOC_MAX_DY_M)
  assert pairs == []          # 同方位, 但对象自报 5m vs 雷达 40m


def test_object_style_box_height_attrs_drive_the_gate():
  """对象侧带 cls/boxHeightPx 属性时, 二级门优先用框高测距。"""
  radar = [R(40.0, 0.0)]
  obj = VO(x=40.0, y=0.0, cls="person", box_height_px=FY * 1.7 / 5.0)  # 框高说 5m
  pairs, _ = nearest_pairs_by_bearing(radar, [obj], FY,
                                      C.ASSOC_MAX_DBEARING, C.ASSOC_MAX_DY_M)
  assert pairs == []


def test_dict_without_gate_inputs_skips_secondary_gate():
  """只有 bearing 的 dict: 无 cls/boxHeightPx 也不暴露 x -> 跳过二级门, 纯方位匹配。"""
  radar = [R(40.0, 0.0)]
  n, fused, pairs = associate(radar, [{"bearing": 0.0}], fy=FY)
  assert n == 1
  assert fused == []


def test_nearest_pairs_by_bearing_pair_shape():
  radars = [R(20.0, 0.0)]
  dets = [_det(0.0, cls="motorcycle", h_px=FY * 1.7 / 20.0)]
  pairs, matched = nearest_pairs_by_bearing(radars, dets, FY, C.ASSOC_MAX_DBEARING, C.ASSOC_MAX_DY_M)
  assert matched == {0}
  radar, obj, db, cls = pairs[0]
  assert radar is radars[0] and obj is dets[0]
  assert db == pytest.approx(0.0, abs=1e-12)
  assert cls == "motorcycle"
