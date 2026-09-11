# LANLink 前端重构：sunnylink 1:1 复刻规格

> 本文件是实现依据。每条都来自**实地勘察**：逐页看 https://www.sunnylink.ai
> （2026-09-11，设备 08b5d05e44b482c1，offroad）+ 读 `upstream/master` 的
> `openpilot/sunnypilot/sunnylink/`。不含推测；未验证的点显式标注 [未验证]。

## 1. 上游参数读写契约（upstream/master）

sunnylink 前端**不直接读写 params**，全部经 athena JSON-RPC
（`openpilot/sunnypilot/sunnylink/athena/sunnylinkd.py`）。四个方法：

| RPC | 作用 | 返回/入参 |
|---|---|---|
| `getParamsAllKeys()` | 全部 key 名 | `list[str]`（`all_keys()` 解 utf-8） |
| `getParamsMetadata()` | settings_ui.json + 实时 capabilities | gzip+base64 的单个 JSON |
| `getParams(keys, compression)` | 批量读值 | 每项 `{key, value(b64), type(int), is_compressed}` |
| `saveParams(dict, compression)` | 批量写 | 无返回；逐 key 跳过 blocked |

要点（决定我们 API 的语义）：

1. **批量，不是单 key**。前端一次取回整页所需的值，不是 N 个请求。
   我们的 `/api/params/_all` + `/api/settings_ui` 已对应 `getParams` + `getParamsMetadata`。
2. **`getParamsMetadata` 把 capabilities 塞进 schema 一起返回**，还附带
   `capability_labels` / `default_model` / `default_big_model` / `chestnut_active`。
   我们拆成 `/api/settings_ui` + `/api/capabilities` 两个接口，语义等价。
3. **写入后自增版本计数**：`ParamsVersion += 1`，供前端察觉设备端改动。
   我们用 `LanLinkParamsVersion`（`params_api._bump_version`），已对齐。
4. **BLOCKED_PARAMS 逐 key 静默跳过**，不是整批失败。上游 9 项：
   `AdbEnabled` `CompletedSunnylinkConsentVersion` `CompletedTrainingVersion`
   `GithubUsername` `GithubSshKeys` `HasAcceptedTerms` `HasAcceptedTermsSP`
   `OnroadCycleRequested` `ParamsVersion`。
   我们继承并扩充（`params_api.BLOCKED_PARAMS`），且改为**返回 403**
   而非静默跳过——局域网单用户场景下，静默失败比报错更危险。
5. **未知 key 过滤而非报错**：`getParams` 用 `available_keys` 先过滤。
   这也是 `Params.get()` 对未知 key 抛 `UnknownKeyName` 的原因——
   我们的 `read_param` 已补上存在性检查（设备实测 500 → 404）。

结论：**我们现有后端 API 与上游契约语义一致**，无需改后端。前端按
`/api/settings_ui` + `/api/capabilities` + `/api/params/_all` 三件套渲染。

## 2. 页面清单（逐页实地勘察）

侧栏结构（实测 DOM 顺序）：

```
SUNNYLINK
  Home              /dashboard
  My Devices        /dashboard/devices
DEVICE SETTINGS
  Device            /dashboard/settings/device
  Toggles           /dashboard/settings/toggles
  Models            /dashboard/settings/models
  Steering          /dashboard/settings/steering
  Cruise            /dashboard/settings/cruise
  Visuals           /dashboard/settings/visuals
  Display           /dashboard/settings/display
  Maps              ← 不做（用户明确排除）
  Vehicle           /dashboard/settings/vehicle
  Software          /dashboard/settings/software
  Developer         /dashboard/settings/developer
  [Migration Wizard] [Pair Device]   ← 云功能，局域网不适用
  [用户名按钮]                        ← 云账号，不适用
```

各页实测内容（分节 → 条目）：

- **Device**：General（Always Offroad Mode + Exit Offroad 按钮、Wake Up Behavior
  下拉、Quiet Mode、Onroad Uploads、Max Time Offroad 下拉）、Language（下拉）
- **Toggles**：核心开关（Enable sunnypilot [REBOOT TO APPLY]、Lane Departure
  Warnings、Always-On DM、Use Metric System）、Recording（Record Driver Camera
  [REBOOT]、Record Microphone Audio [REBOOT]）
- **Models**：Model Behavior（Lane Turn Desires、Adjust Lane Turn Speed
  [ADVANCED][滑块 0–20]）、Live Learning Steer Delay（+ Adjust Software Delay
  [滑块 0.05–0.50]）、Lateral Control（NNLC）、Camera（Camera Offset
  [滑块 -0.35–0.35]）
- **Steering**：MADS（开关 + **MADS Settings 子页入口**）、Blinker Control
  （Pause Lateral with Blinker、Minimum Speed [滑块 0–255 km/h]、Post-Blinker
  Delay [滑块 0–10s]）、Torque Control（Enforce Torque + **Torque Settings 子页**）、
  Lane Change（Auto Lane Change by Blinker [下拉 Nudge]、Road Edge Detection、
  BSM Delay）
- **Cruise**：Experimental Mode、Disengage on Accelerator、Driving Personality
  [三段按钮 Aggressive/Standard/Relaxed]、Custom ACC Speed Intervals
  （开关 + **子页**）、Speed Limits（**Speed Limit Settings 子页**）
- **Visuals**：HUD Elements（7 个开关 + Display Metrics Below Chevron [下拉]）、
  Developer UI Info（Developer UI [下拉 Off]、True Speed、Hide Speedometer）、
  Alerts & Extras（Green Light Alert、Lead Departure Alert、Tesla Rainbow Mode）
- **Display**：Brightness & Timeout（Onroad Brightness [下拉]、Brightness Delay
  [下拉]、Interactivity Timeout [下拉]）
- **Vehicle**：车辆卡片（"No vehicle detected / Not fingerprinted" + Select 按钮）
- **Software**：Updates（Disable Updates [ADVANCED]）
- **Developer**：Connectivity（Enable ADB [DEVICE ONLY]、Enable SSH [DEVICE ONLY]、
  Joystick Debug、UI Debug）、Advanced Settings（Show Advanced Controls、
  GitHub Runner [ADVANCED]、copyparty [ADVANCED]、Quickboot [ADVANCED]）
- **My Devices**：设备列表（名称/dongle id/在线状态/选中态）+ Pair New Device

## 3. 组件清单（实测 DOM 得出）

| 组件 | 实测形态 | 我们 schema 的 widget |
|---|---|---|
| 开关 | `button[role=switch]` 44×26 靠右 | `toggle` |
| 下拉 | `button[role=combobox]`，右对齐 | `option` |
| 分段按钮 | 横排 button 组（Aggressive/Standard/Relaxed） | `multiple_button` |
| 滑块 | `input[type=range]` + 左右 ±40×40 圆按钮 + 居中数值 | `option` + `min/max/step`（见下） |
| 子页入口 | 整行 48px button，点进二级页 | `sub_panels` |
| 说明文字 | 标题下方灰色小字 | `description` |
| 徽章 | `UNAVAILABLE` `ADVANCED` `REBOOT TO APPLY` `DEVICE ONLY` | 由 enablement 推导 |
| 详情按钮 | `button[aria-label^="More details about"]` ⓘ | 长描述折叠 |
| 同步状态 | 标题旁 `Refresh` / `Synced` 圆按钮 | 我们用 paramsVersion 轮询 |
| 搜索 | 顶栏 `Search settings…` + ⌘K | 全局设置搜索 |
| 顶部横幅 | Always Offroad 警告条 + Disable 按钮 | 状态条 |

**滑块没有独立 widget 类型**（已核实，不是推测）：上游
`_schemas/page.schema.json` 的 widget enum 只有
`["toggle","option","multiple_button","button","info"]`。
`option` 按**是否带 `min`/`max`/`step`** 分叉渲染：

```jsonc
// 带 min/max/step → 渲染成滑块（± 按钮 + range + 居中数值）
{"key":"CameraOffset","widget":"option","min":-0.35,"max":0.35,"step":0.01,
 "unit":"meters"}
// 带 options[] → 渲染成下拉
{"key":"AutoLaneChangeTimer","widget":"option","options":[...]}
```

`unit` 有两种形态，都要支持：字符串（`"meters"`）或按单位制分叉
（`{"metric":"km/h","imperial":"mph"}`，跟随 `IsMetric` 切换）。

**禁用态表达**：`disabled` 属性 + `opacity-[0.4]`/`opacity-50` + `UNAVAILABLE` 徽章。
不隐藏，而是**置灰并说明原因**——这点必须复刻，否则用户以为功能消失了。

## 4. 视觉令牌（`browser.inspect` 实测计算样式）

```
body        background #101010   color #ececec
aside       background #181818   border-right 1px #2c2c2c
字体        "Inter Variable", Inter, -apple-system, system-ui, ...
正文        16px / line-height 24px / weight 400
侧栏项      255×40，选中态 background var(--sl-accent-muted)
开关        44×26
滑块按钮    40×40
子页入口行  766×48
内容区宽    768px 居中（main 内容 x=480..1248 @1440 视口）
```

站点自身用 CSS 变量 `--sl-text-1` / `--sl-accent-muted` 等，我们照抄这套
命名，暗黑极客风基底一致，再在其上做更现代的处理（用户要求"可以更漂亮"）。

## 5. 与本地 schema 的差异（必须处理）

本地 `settings_ui.json`（46KB，8 panel / 66 item）对比上游（9 panel）：

| 差异 | 事实 | 处置 |
|---|---|---|
| 缺 `cruise` panel | 上游有 cruise（4 节 14 项），本地无 | 从上游 schema 补 |
| 滑块 | 上游用 `option` + `min/max/step`，无独立 widget（已核实 enum） | 前端按 min/max 分叉渲染 |
| 本地 widget 只有 3 种 | toggle 46 / option 12 / multiple_button 8；上游另有 `info`(1) `button` | 补 info/button 分支 |
| 本地 panel 标签是中文 | 转向/显示/视觉/通用/设备/软件/开发者/模型 | 保留中文（本地化更好） |
| Maps | 上游侧栏有 | **不做**（用户明确排除） |

`vehicle_settings` 含 hyundai/subaru/tesla/toyota，但 lean fork 的 opendbc
只有 Toyota——`status_snapshot.py:95` 注释已说明另三个品牌恒 False，
对应 vehicle_settings 永不显示。前端无需特殊处理。

## 6. 交付形态（已与用户确认）

设备无 Node、无 CI 构建步骤，`release_files.py` 只发 `git ls-files` 的
已 tracked 文件 → **Vite 产物 `dist/` 必须提交进仓库**，并加**源码 hash
陈旧校验**（仿 `release_lib.py` 的 `native_hash`）：源码改了没重新构建就报错，
不允许设备静默跑旧 UI。
