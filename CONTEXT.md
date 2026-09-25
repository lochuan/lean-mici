# sunnypilot（模型几何与安装偏移）

本 fork 的感知/决策链路围绕「模型几何的解释权」展开：模型几何、雷达目标、视觉检测各自携带坐标系语义，本词汇表定名这些概念，供 issue、提案、测试命名取词。

## Language

### 几何与坐标系

**模型几何**（model geometry）:
modelV2 输出的道路几何（车道线、路沿等点列），其原始参照系为相机系。
_Avoid_: 车道线数据、laneLines（代码字段名，非领域词）

**车体系**（vehicle frame）:
本项目几何解释的权威参照系：原点在前保险杠，纵向 `dRel` 向前为正、横向 `yRel` 左正。
_Avoid_: 车头系、保险杠系（均指同一物，统一叫车体系）

**相机系**（camera frame）:
以挡风玻璃后方相机为原点的参照系，模型几何的原始坐标系。
_Avoid_: 视觉系

**投影边界换算口**（projection boundary conversion point）:
车内 UI 渲染器进透视投影矩阵前的唯一坐标换算点（`_map_to_screen` / `_map_line_to_polygon` 入口）：把车体系换回相机系（补回安装偏移），并叠加显示偏移；换算口之外的渲染器数据一律车体系。
_Avoid_: 投影换算、坐标变换（太泛，不指明换算方向与边界）

**毫米波雷达**（radar）:
装在保险杠后方的测距传感器，测量值天然在车体系。
_Avoid_: 雷达目标（那是雷达的测量结果，不是传感器本身）

**视觉相机**（vision camera）:
装在挡风玻璃后方的相机，比毫米波雷达更靠后。
_Avoid_: 摄像头、前视相机

### 标定与精修

**安装偏移**（`CAMERA_TO_FRONT`，Params 键 `CameraToFront`）:
视觉相机与前保险杠之间的纵向距离，是相机系与车体系之间的纵向换算量。
运行时值的唯一读点是 `model_geometry.read_camera_to_front`（未落盘回退出厂默认）。
_Avoid_: CameraOffset（那是上游的相机偏移软件修正，另一回事）、外参

**安装偏移精修**:
通过路测数据拟合纵向安装偏移的会话式流程，产出安装偏移值。
_Avoid_: 标定（标定指 `extrinsicsCalibration` 的 pitch/yaw/roll 姿态拟合，与纵向安装偏移是两件事）

**显示偏移**（`CameraOffset`）:
上游自带的相机偏移软件修正参数，用于设备安装不正时的显示修正，与 eagled 的几何解释无关。
_Avoid_: 安装偏移
