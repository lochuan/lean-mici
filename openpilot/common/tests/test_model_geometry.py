"""model_geometry 单元测试：相机系模型几何 → 车体系（换算的唯一解释权）。

坐标事实（spec #1 / 票 #2，见 CONTEXT.md 词条）：
- 相机系（modelV2）：x 以相机为原点，y 右正。
- 车体系：x 以前保险杠为原点（dRel 语义），y 左正（yRel 语义）。
- 安装偏移 camera_to_front：相机在保险杠后方多远，相机系 x 减去它到车体系。

期望值全部为手算 worked example，不从实现反推。
"""
from types import SimpleNamespace as NS

from openpilot.common.model_geometry import (
  VehicleFrameGeometry,
  VehicleFrameLine,
  geometry_to_vehicle_frame,
  line_to_vehicle_frame,
  vehicle_to_camera_frame,
)

CTF = 1.5  # 安装偏移，默认出厂值


def _line(x, y, z=None):
  return NS(x=x, y=y, z=z if z is not None else [0.0] * len(x))


def test_camera_frame_line_converts_to_vehicle_frame():
  # 相机前 10m、右侧 2m（模型 y 右正 → +2.0）的点：
  # = 保险杠前 8.5m、右侧 2m → 车体系 y 左正 = -2.0
  line = line_to_vehicle_frame([10.0], [2.0], [0.0], camera_to_front=CTF)
  assert line == VehicleFrameLine(x=(8.5,), y=(-2.0,), z=(0.0,))

  # 相机前 20m、左侧 3m（模型 y 右正 → -3.0）= 保险杠前 18.5m、左侧 3m
  line = line_to_vehicle_frame([20.0], [-3.0], [1.2], camera_to_front=CTF)
  assert line == VehicleFrameLine(x=(18.5,), y=(3.0,), z=(1.2,))


def test_whole_frame_geometry_converts_lanes_edges_and_path():
  model = NS(
    laneLines=[_line([10.0, 20.0], [2.0, 2.5]), _line([10.0], [1.8]),
               _line([10.0], [-1.8]), _line([10.0], [-2.0])],
    roadEdges=[_line([10.0], [3.5]), _line([10.0], [-3.5])],
    position=NS(x=[10.0, 30.0], y=[0.5, -1.5], z=[0.0, 0.0]),
  )
  geo = geometry_to_vehicle_frame(model, camera_to_front=CTF)
  assert geo == VehicleFrameGeometry(
    lane_lines=(
      VehicleFrameLine(x=(8.5, 18.5), y=(-2.0, -2.5), z=(0.0, 0.0)),
      VehicleFrameLine(x=(8.5,), y=(-1.8,), z=(0.0,)),
      VehicleFrameLine(x=(8.5,), y=(1.8,), z=(0.0,)),
      VehicleFrameLine(x=(8.5,), y=(2.0,), z=(0.0,)),
    ),
    road_edges=(
      VehicleFrameLine(x=(8.5,), y=(-3.5,), z=(0.0,)),
      VehicleFrameLine(x=(8.5,), y=(3.5,), z=(0.0,)),
    ),
    path=VehicleFrameLine(x=(8.5, 28.5), y=(-0.5, 1.5), z=(0.0, 0.0)),
  )


def test_missing_fields_degrade_to_none_without_raising():
  geo = geometry_to_vehicle_frame(NS(), camera_to_front=CTF)
  assert geo == VehicleFrameGeometry(
    lane_lines=(None, None, None, None),
    road_edges=(None, None),
    path=None,
  )


def test_empty_and_malformed_lines_degrade_to_none_items():
  model = NS(
    laneLines=[_line([], []), _line([10.0, 20.0], [1.0]),  # 空列表 / x-y 长度不一致
               NS(x=[10.0], y=None, z=[0.0]), _line([10.0], [1.0])],
    roadEdges=[],
    position=NS(x=[], y=[], z=[]),
  )
  geo = geometry_to_vehicle_frame(model, camera_to_front=CTF)
  assert geo.lane_lines[0] is None
  assert geo.lane_lines[1] is None
  assert geo.lane_lines[2] is None
  assert geo.lane_lines[3] == VehicleFrameLine(x=(8.5,), y=(-1.0,), z=(0.0,))
  assert geo.road_edges == (None, None)
  assert geo.path is None


def test_missing_z_defaults_to_ground_level():
  line = line_to_vehicle_frame([10.0], [2.0], None, camera_to_front=CTF)
  assert line == VehicleFrameLine(x=(8.5,), y=(-2.0,), z=(0.0,))


def test_empty_z_list_defaults_to_ground_level():
  # capnp 折线未填 z 时是空列表而非 None（真实 modelV2 形态）
  line = line_to_vehicle_frame([10.0, 20.0], [2.0, 2.0], [], camera_to_front=CTF)
  assert line == VehicleFrameLine(x=(8.5, 18.5), y=(-2.0, -2.0), z=(0.0, 0.0))


# --- 逆换算（投影边界口，票 #5） ---------------------------------------------------

def test_camera_frame_roundtrip_is_identity():
  # 出口换算与投影边界换算必须共用一套语义：出去再回来 = 恒等
  cam = (10.0, -2.0, 0.3)   # 相机系（x 前正、y 右正）
  veh = line_to_vehicle_frame([cam[0]], [cam[1]], [cam[2]], camera_to_front=CTF)
  assert vehicle_to_camera_frame(veh.x[0], veh.y[0], veh.z[0], CTF) == cam


def test_projection_boundary_places_lead_at_true_distance():
  # 前车 dRel=20（车体系，保险杠原点）→ 投影矩阵该吃相机系 21.5
  #（相机在保险杠后方 1.5m）。这正是 UI 前车标记的历史帧混用处。
  assert vehicle_to_camera_frame(20.0, 1.2, 0.0, CTF) == (21.5, -1.2, 0.0)
