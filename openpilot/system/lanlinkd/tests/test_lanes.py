"""lanes.py 单元测试：modelV2 → 车道几何快照（俯视图数据源）。

设计要点（2026-09-24 spec）：
- 纯函数 lane_snapshot：modelV2 帧 + 接收时刻 → lanes dict / None（超龄）。
- 重采样到固定网格 x = 0,5,...,60（与前端显示范围一致）。
- y 取负号换算到雷达左正约定（modelV2 y 右正）。
- 字段缺失/空列表 → 对应线为 None，绝不抛异常（模型桩形态千差万别）。
"""
import time
from types import SimpleNamespace as NS

import pytest

from openpilot.system.lanlinkd import lanes


GRID = lanes.LANE_GRID_X


def _line(y_model: list[float], x: list[float] | None = None) -> NS:
  x = x if x is not None else [0.0, 50.0]
  return NS(x=x, y=y_model)


_line_outer_left = _line([-5.25, -5.25])
_line_outer_right = _line([5.25, 5.25])


def _model_v2(left=(-1.75, -1.75), right=(1.75, 1.75), prob=(0.1, 0.9, 0.1, 0.1),
              std=(0.5, 0.1, 0.5, 0.5), edge_std=(0.05, 0.05),
              path_y=(0.0, 0.0), path_y_std=(0.1, 0.1)) -> NS:
  """标准 modelV2 桩：直道，本车道 ±1.75m。

  y 序列与 x=[0,50] 对齐；laneLines[0]/[3] 是外侧车道线（远左/远右），
  [1]/[2] 是本车道左右边界。
  """
  return NS(
    laneLines=[_line_outer_left, _line(left), _line(right), _line_outer_right],
    laneLineProbs=list(prob),
    laneLineStds=list(std),
    roadEdges=[_line(left), _line(right)],
    roadEdgeStds=list(edge_std),
    position=NS(x=[0.0, 50.0], y=list(path_y), yStd=list(path_y_std)),
  )


# --- 网格与符号 -------------------------------------------------------------------

def test_resamples_to_shared_grid_with_radar_y_sign():
  """本车道左边界 y_model=-1.75（右正）→ 快照 +1.75（左正），13 点网格。"""
  snap = lanes.lane_snapshot(_model_v2(), recv_mono=100.0, now_mono=100.05)
  assert GRID == tuple(range(0, 65, 5)) and len(GRID) == 13
  left = snap["laneLines"][1]
  assert all(y == pytest.approx(1.75) for y in left["y"])


def test_curved_line_is_interpolated_at_grid_x():
  """y_model 在 x=0 处 -1.75、x=40 处 -2.55（线性）→ 网格 x=20 处 +2.15。"""
  m = _model_v2()
  m.laneLines[1] = _line([-1.75, -2.55], x=[0.0, 40.0])
  snap = lanes.lane_snapshot(m, recv_mono=100.0, now_mono=100.05)
  y20 = snap["laneLines"][1]["y"][GRID.index(20)]
  assert y20 == pytest.approx(2.15)


def test_quality_fields_pass_through_per_line():
  snap = lanes.lane_snapshot(_model_v2(), recv_mono=100.0, now_mono=100.05)
  assert snap["laneLines"][1]["prob"] == pytest.approx(0.9)
  assert snap["laneLines"][1]["std"] == pytest.approx(0.1)
  assert snap["laneLines"][2]["prob"] == pytest.approx(0.1)   # 右边界（外线）
  assert snap["roadEdges"][0]["std"] == pytest.approx(0.05)


def test_path_resampled_with_y_std():
  m = _model_v2(path_y=(0.0, -1.0), path_y_std=(0.05, 0.4))
  snap = lanes.lane_snapshot(m, recv_mono=100.0, now_mono=100.05)
  # y_model=-1（右偏 1m）→ 左正约定为 +1；x=25 处插值 +0.5
  assert snap["path"]["y"][GRID.index(25)] == pytest.approx(0.5)
  assert snap["path"]["std"][GRID.index(25)] == pytest.approx(0.225)


# --- 缺失与超龄 -------------------------------------------------------------------

def test_missing_line_entry_becomes_none_not_crash():
  m = _model_v2()
  m.laneLines[0] = NS(x=[], y=[])       # 外侧左线没有数据
  del m.roadEdgeStds                     # 整个字段缺失
  snap = lanes.lane_snapshot(m, recv_mono=100.0, now_mono=100.05)
  assert snap["laneLines"][0] is None
  assert snap["roadEdges"][0] is not None and snap["roadEdges"][0]["std"] is None


def test_duck_typed_model_without_any_geometry_yields_all_none():
  snap = lanes.lane_snapshot(NS(), recv_mono=100.0, now_mono=100.05)
  assert snap["laneLines"] == [None, None, None, None]
  assert snap["roadEdges"] == [None, None]
  assert snap["path"] is None


def test_stale_modelV2_returns_none():
  assert lanes.lane_snapshot(_model_v2(), recv_mono=100.0, now_mono=100.0 + lanes.LANE_MAX_AGE_S + 0.01) is None
  assert lanes.lane_snapshot(_model_v2(), recv_mono=100.0, now_mono=100.0 + lanes.LANE_MAX_AGE_S) is not None


# --- LaneCache 集成（真实 SubMaster 链路） ----------------------------------------

def test_lane_cache_serves_published_model_v2():
  from openpilot.cereal import messaging
  cache = lanes.LaneCache()
  pub = messaging.PubMaster(['modelV2'])
  msg = messaging.new_message('modelV2')
  ml = msg.modelV2
  ml.init('laneLines', 4)
  ml.laneLines[1].x, ml.laneLines[1].y = [0.0, 50.0], [-1.75, -1.75]
  ml.laneLineProbs = [0.1, 0.9, 0.1, 0.1]
  ml.laneLineStds = [0.5, 0.1, 0.5, 0.5]
  ml.position.x, ml.position.y, ml.position.yStd = [0.0, 50.0], [0.0, 0.0], [0.05, 0.05]
  try:
    snap = None
    for _ in range(20):   # 订阅建立前的帧会丢，循环发送直到收到
      pub.send('modelV2', msg)
      snap = cache.snapshot()
      if snap is not None:
        break
      time.sleep(0.05)
  finally:
    cache.stop()
  assert snap is not None
  assert snap["laneLines"][1]["y"][0] == pytest.approx(1.75)


def test_lane_cache_returns_none_before_any_frame():
  cache = lanes.LaneCache()
  try:
    assert cache.snapshot() is None
  finally:
    cache.stop()
