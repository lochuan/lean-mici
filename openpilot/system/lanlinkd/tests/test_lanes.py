"""lanes.py 单元测试：modelV2 → 车道几何快照（俯视图数据源）。

设计要点（2026-09-25 spec #1 / 票 #4）：
- 纯函数 lane_snapshot：modelV2 帧 + 接收时刻 + 安装偏移（注入值）→ lanes dict / None（超龄）。
- 两套同网格几何：corrected = model_geometry(ctf=安装偏移)（车体系，x 前保险杠
  原点）；raw = model_geometry(ctf=0)（与换算前的展示行为逐点一致）。
  叠加视图两套曲线的错位 = 安装偏移（精修仪器自检）。
- 重采样到固定网格 x = 0,5,...,60（与前端显示范围一致）。
- y 左正（model_geometry 出口已换算，本模块不再做符号翻转）。
- 字段缺失/空列表 → 对应线为 None，绝不抛异常（模型桩形态千差万别）。
"""
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openpilot.system.lanlinkd import lanes


GRID = lanes.LANE_GRID_X
CTF = 1.5  # 安装偏移，注入值


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


def _snap(m, ctf=CTF):
  return lanes.lane_snapshot(m, recv_mono=100.0, now_mono=100.05, camera_to_front=ctf)


# --- 网格与符号 -------------------------------------------------------------------

def test_resamples_to_shared_grid_with_left_positive_y():
  """本车道左边界 y_model=-1.75（右正）→ 快照 +1.75（左正），13 点网格。"""
  snap = _snap(_model_v2())
  assert GRID == tuple(range(0, 65, 5)) and len(GRID) == 13
  for key in ("corrected", "raw"):
    left = snap[key]["laneLines"][1]
    assert all(y == pytest.approx(1.75) for y in left["y"])


def test_raw_set_matches_pre_conversion_behaviour():
  """raw 集（ctf=0）与换算前的展示行为逐点一致：模型 y 直接按 x 采样取负。"""
  m = _model_v2()
  m.laneLines[1] = _line([-1.75, -2.55], x=[0.0, 40.0])
  snap = _snap(m)
  y20 = snap["raw"]["laneLines"][1]["y"][GRID.index(20)]
  assert y20 == pytest.approx(2.15)   # -y_model(20) = 1.75 + 20/40*0.8


def test_corrected_set_samples_at_bumper_distance():
  """corrected 集在车体系距离上采样：网格 20m 处取的是相机系 21.5m 的模型值。

  y_model 线性 -1.75→-2.55 over x=0→40（斜率 -0.02/m）:
  y(21.5) = -(1.75 + 21.5*0.02) = -2.18 → 快照 +2.18。
  """
  m = _model_v2()
  m.laneLines[1] = _line([-1.75, -2.55], x=[0.0, 40.0])
  snap = _snap(m)
  y20 = snap["corrected"]["laneLines"][1]["y"][GRID.index(20)]
  assert y20 == pytest.approx(2.18)


def test_overlay_curves_differ_by_the_applied_offset():
  """仪器自检：corrected 曲线 = raw 曲线沿 x 平移安装偏移（叠加视图的语义）。"""
  m = _model_v2()
  m.laneLines[1] = _line([-1.75, -2.55], x=[0.0, 40.0])
  snap = _snap(m)
  raw_y = snap["raw"]["laneLines"][1]["y"]
  cor_y = snap["corrected"]["laneLines"][1]["y"]
  for g in (0, 10, 20, 30):
    # corrected(g) == raw 在 g+ctf 处的插值（线性线，两层插值仍精确）
    y_at_shifted = float(np.interp(g + CTF, list(GRID), raw_y))
    assert cor_y[GRID.index(g)] == pytest.approx(y_at_shifted, abs=1e-9)


def test_quality_fields_pass_through_per_line():
  snap = _snap(_model_v2())
  assert snap["corrected"]["laneLines"][1]["prob"] == pytest.approx(0.9)
  assert snap["corrected"]["laneLines"][1]["std"] == pytest.approx(0.1)
  assert snap["corrected"]["laneLines"][2]["prob"] == pytest.approx(0.1)   # 右边界（外线）
  assert snap["corrected"]["roadEdges"][0]["std"] == pytest.approx(0.05)


def test_path_resampled_with_y_std():
  m = _model_v2(path_y=(0.0, -1.0), path_y_std=(0.05, 0.4))
  snap = _snap(m)
  # raw（ctf=0）:y_model=-1（右偏 1m）→ 左正 +1；x=25 处插值 +0.5
  assert snap["raw"]["path"]["y"][GRID.index(25)] == pytest.approx(0.5)
  assert snap["raw"]["path"]["std"][GRID.index(25)] == pytest.approx(0.225)


# --- 缺失与超龄 -------------------------------------------------------------------

def test_missing_line_entry_becomes_none_not_crash():
  m = _model_v2()
  m.laneLines[0] = NS(x=[], y=[])       # 外侧左线没有数据
  del m.roadEdgeStds                     # 整个字段缺失
  snap = _snap(m)
  for key in ("corrected", "raw"):
    assert snap[key]["laneLines"][0] is None
    assert snap[key]["roadEdges"][0] is not None and snap[key]["roadEdges"][0]["std"] is None


def test_duck_typed_model_without_any_geometry_yields_all_none():
  snap = _snap(NS())
  for key in ("corrected", "raw"):
    assert snap[key]["laneLines"] == [None, None, None, None]
    assert snap[key]["roadEdges"] == [None, None]
    assert snap[key]["path"] is None


def test_stale_modelV2_returns_none():
  # modelV2 新鲜度走 common.stream_gate（登记阈值 1s），超龄/边界语义同源
  from openpilot.common.stream_gate import MAX_AGE_S
  assert lanes.lane_snapshot(_model_v2(), recv_mono=100.0, now_mono=100.0 + MAX_AGE_S["modelV2"] + 0.01,
                             camera_to_front=CTF) is None
  assert lanes.lane_snapshot(_model_v2(), recv_mono=100.0, now_mono=100.0 + MAX_AGE_S["modelV2"],
                             camera_to_front=CTF) is not None


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
      snap = cache.snapshot(camera_to_front=CTF)
      if snap is not None:
        break
      time.sleep(0.05)
  finally:
    cache.stop()
  assert snap is not None
  assert snap["corrected"]["laneLines"][1]["y"][0] == pytest.approx(1.75)


def test_lane_cache_returns_none_before_any_frame():
  cache = lanes.LaneCache()
  try:
    assert cache.snapshot(camera_to_front=CTF) is None
  finally:
    cache.stop()
