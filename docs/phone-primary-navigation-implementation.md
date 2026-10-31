# sunnypilot 手机唯一导航位置源：实施与验证记录

日期：2026-10-24。状态：车机实现已完成，工作树未提交，供代码审查；未达到真车联合验收或可部署声明。

## 范围与基线

- 工作树：`/Users/kevin/.paseo/worktrees/1ovwqbgj/phone-primary-navigation-sunnypilot`。
- 分支：`fm/phone-primary-navigation-sunnypilot`。
- HEAD：`1936633813f106af57c43df336b97f2edcaecfc0`，与指定本地 nav-test 基线完全相同。开始时工作区干净，未 rebase 或更换基线。
- 权威设计：`/Users/kevin/.paseo/plugin-data/firstmate/home/data/phone-primary-navigation-design/report.md`。
- 仅修改本工作树。没有修改手机仓、设备、全局 Python 环境或其它 openpilot GNSS 功能；没有提交、推送、安装、部署、merge 或启动子代理。
- 按 Graphify 技能检查现有图，本树和 openpilot 根没有现有 `graphify-out/`。本次以源码和调用点核实链路，没有重建全仓图，Graphify 提取成本为 0。

## 最终行为

1. BGM1 固定为 v4。FRAME 头为 152 B，最小线长 172 B。速度在 @144，yaw 在 @146，flags 在 @148，@149–151 保留零。REPLY/HELLO/ERR 仍为 8480/48/40 B。
2. 删除 FRAME GNSS、GnssSample、set/latest_gnss、最后 fix 缓存、车机 GPS 精度迟滞与 LastGPSPosition 的本链路生产/消费。bigmodeld 的 SubMaster 只保留 extrinsicsCalibration/modelDataV2SP/deviceMotion。
3. deviceMotion 的标定转换、顺时针正 yaw、速度绝对值与倒车标志、100 ms 源期限、sensorsOK/inputsOK/posenetOK 和非有限值检查继续生效。没有手机定位时，车速/yaw 仍可正常上行。
4. 所有 parse_* 先验证 16 B 消息头的版本，再检查完整 payload。MsgFramer 在旧 HELLO/REPLY/ERR 的前 16 B 即报版本错误，main 维持 version_mismatch 并断连/清空。没有 v3 兼容路径。
5. navStateSP 的 ordinal @2 改为 phoneLocationOk，不增加位置坐标；服务频率/记录方式不变。NAV_STATE 和 CURVE 的年龄独立缓存及判期。
6. MapTargetWriter 仅依赖手机健康位与沿路线距离，生成带 C4 BOOTTIME 推定采样时间和期限的 envelope：sample=t_eof−CURVE.age，expiry=min(sample+2s,t_eof−NAV_STATE.age+2s)。未知年龄、下溢、未来、溢出、失效手机位置、断连、重算或无效路线均清目标。
7. 保留新增/提速/距离变化最多 2 Hz、不变 1 Hz、删除/降速/失效立即写的策略；距离参与内容比较。参数 I/O 仍由非实时 calib 线程执行。排序损坏立即清空；空结果也按 1 Hz 刷新。
8. SCC-map 接受新 object 或清空 []，拒绝旧非空数组。验证时间、最多 8 点、0..600 m 单调距离、合法目标坐标和有限正速度；坏 JSON/深层嵌套/坏字段不逃逸到控制循环，并限流记录解析失败原因。
9. SCC-map 用当前车速补偿接收延迟，再按现有控制拍累计前进距离。新快照重置累计；重复快照既不重置累计，也不能延长期限；乱序、过期、时间倒退、倒车、无效 carState 和非有限/负速度清目标。C4 明确使用 CLOCK_BOOTTIME，宿主测试注入假时钟。
10. 原 TARGET_JERK/ACCEL/OFFSET、同坐标目标保持、删除/降速及 override 状态机保留。没有 haversine 或“地理最近点后截断”，前弯和 U 形路线不再受直线距离选择影响。
11. manager 保留上下路和 writer-gone 清理，仅管理 MapTargetVelocities。全局仍存在的 GPS/Params 定义不作机械删除。
12. 导航卡显示 `Phone location unavailable`，手机健康位失效时隐藏旧转向/ETA。短卡高度保持 50，长提示按测量宽度扩展并保持右侧对齐。可运行文案页：[phone-location-card.html](phone-location-card.html)。页面使用系统字体，是示意而非真实 raylib/车机验收；未截图或读取图像。

## TDD 与差异复核

按已批准公开 seam 分片推进，先观察失败，再实现：

| seam | 实际观察到的 RED | GREEN 与回归 |
|---|---|---|
| wire codec | v4/152/172 常量及旧 16 B FRAME 版本优先失败；后续 HELLO/ERR 同样暴露长度优先错误 | 所有 parse_* 版本优先、motion 规范化、偏移与 golden |
| MetaProvider | wire 类型切换后，纯 Vehicle 速度用例无法由旧 GNSS 输出实现 | 纯 motion、100 ms 边界、标定/有效性、NaN、饱和、yaw 符号、采样相位、零分配 |
| MapTargetWriter/NavLink | 新期限 envelope seam 不存在；排序错误清空和空结果 1 Hz 用例各观察到行为失败 | 独立 phone/CURVE 年龄、链路清空、距离内容刷新、删点/降速、1/2 Hz、共用八点 JSON |
| SCC-map | 新 object 输入/时钟 seam 不存在；2 Hz 间隔距离不递减；carState 无效/倒车仍生成目标；深层 JSON 抛 RecursionError | object 校验、传输延迟、积分/停止/新快照重置、过期/乱序/重复、不遗漏前弯、保持/降速/override |
| schema/卡片 | 新 phoneLocationOk 字段不存在；旧提示仍为 GPS lost | rlog 往返与卡片纯格式化通过；真实渲染测试受缺失依赖阻断 |

已复核全部生产 diff、测试/fixture、schema、文案和 README；同时检索删除符号的调用点。复核补修了 HELLO/ERR 长度先于版本、排序坏点清空延迟、无效空结果 2 Hz 重写、长文案固定宽度溢出。没有未处理的已知逻辑审查发现；设备与完整构建缺口见下。

## 环境与命令

以下命令的 `CAR_ROOT` 为上述车机工作树，`PHONE_ROOT` 为手机配套工作树：

```sh
CAR_ROOT=/Users/kevin/.paseo/worktrees/1ovwqbgj/phone-primary-navigation-sunnypilot
PHONE_ROOT=/Users/kevin/.paseo/worktrees/2n3c53lx/phone-primary-navigation-chipmunk
PYTHON_BIN=/Users/kevin/Documents/Projects/sunnypilot/.venv/bin/python
cd "$CAR_ROOT"
git branch --show-current
git rev-parse HEAD
git merge-base --is-ancestor 1936633813f106af57c43df336b97f2edcaecfc0 HEAD
```

结果：正确分支/HEAD，祖先校验退出 0。Python 为已有 3.12.13 环境，没有安装依赖。本工作树缺本地 libparams_c.dylib；宿主验证临时复用了主仓现有构建产物：

```sh
cmp openpilot/common/params.cc /Users/kevin/Documents/Projects/sunnypilot/openpilot/common/params.cc
cmp openpilot/common/params_c.cc /Users/kevin/Documents/Projects/sunnypilot/openpilot/common/params_c.cc
cmp openpilot/common/params_keys.h /Users/kevin/Documents/Projects/sunnypilot/openpilot/common/params_keys.h
cp /Users/kevin/Documents/Projects/sunnypilot/openpilot/common/libparams_c.dylib openpilot/common/libparams_c.dylib
```

三个 cmp 均退出 0。使用预编译库不等于在本树完整构建通过。测试结束后移除本次复制的库，工作树不交付二进制。测试写入使用 `/tmp`；首次旧测试因默认用户日志目录受沙箱限制失败，随后以 LOG_ROOT/PARAMS_ROOT 明确隔离，成功结果如下。

### C++ 全套与 golden

```sh
cd "$CAR_ROOT/openpilot/selfdrive/bigmodeld"
clang++ -std=c++17 -O1 test_bigmodeld.cc frame_codec.cpp frame_meta.cpp \
  frame_scheduler.cpp uplink_sender.cpp server_locator.cpp nav_outputs.cpp \
  -o /tmp/phone-nav-test-bigmodeld
BGM1_GOLDEN_DIR="$PHONE_ROOT/app/tests/fixtures" /tmp/phone-nav-test-bigmodeld
```

最终结果：**5524 checks, 0 fails**；未设置 BGM1_GOLDEN_SKIP，没有跳过 golden。覆盖调度、发送器假 socket、部分写、断流/重连、分片/黏包、codec、MetaProvider、NavLink、MapTargetWriter。不是设备 socket/纵向实车证明。

```sh
clang++ -std=c++17 -O1 test_warp_lut.cc warp_pack.cpp frame_meta.cpp frame_codec.cpp \
  -o /tmp/phone-nav-test-warp-lut
/tmp/phone-nav-test-warp-lut
clang++ -std=c++17 -O1 test_warp_golden.cc warp_pack.cpp -o /tmp/phone-nav-test-warp-golden
/tmp/phone-nav-test-warp-golden
```

结果：warp_lut ALL PASS；warp golden ALL PASS；m1/m2 各 2 px 差异、m3 0 px，均在既有容差内。

### Python 相关完整可行套件

```sh
cd "$CAR_ROOT"
LOG_ROOT=/tmp/phone-nav-logs PARAMS_ROOT=/tmp/phone-nav-params "$PYTHON_BIN" -m pytest \
  openpilot/selfdrive/bigmodeld/tests/test_nav_state_sp.py \
  openpilot/sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests \
  openpilot/system/manager/tests/test_map_params.py \
  openpilot/selfdrive/ui/tests/test_nav_card.py -q
```

结果：**227 passed**，其中 schema 4、完整 SCC 目录 57、manager 7、卡片纯逻辑 159，无跳过。后续 manager fixture 仅缩短 JSON 字面量以满足 Ruff，单独重跑该文件 **7 passed**。

```sh
LOG_ROOT=/tmp/phone-nav-logs PARAMS_ROOT=/tmp/phone-nav-params "$PYTHON_BIN" -m pytest \
  tools/release/test_release_lib.py tools/release/test_local_release.py \
  tools/release/test_local_release_device.py -q
```

结果：**53 passed, 10 subtests passed**。这是 release 脚本宿主回归，没有执行设备部署操作。

```sh
"$PYTHON_BIN" -m ruff check \
  openpilot/selfdrive/bigmodeld/tests/test_nav_state_sp.py \
  openpilot/sunnypilot/selfdrive/controls/lib/smart_cruise_control/map_controller.py \
  openpilot/sunnypilot/selfdrive/controls/lib/smart_cruise_control/smart_cruise_control.py \
  openpilot/sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_map_controller.py \
  openpilot/system/manager/map_params.py openpilot/system/manager/tests/test_map_params.py \
  openpilot/selfdrive/ui/sunnypilot/nav_card.py \
  openpilot/selfdrive/ui/sunnypilot/mici/onroad/nav_card_renderer.py \
  openpilot/selfdrive/ui/tests/test_nav_card.py openpilot/selfdrive/ui/mici/tests/test_nav_card_render.py
```

结果：**All checks passed**。全部已改 Python 文件也经 py_compile，结果 **All changed Python files compile**。HTML 的内嵌 JS 通过 Node 假 DOM 状态自检，结果 **HTML state self-check passed**；没有浏览器图像检查。

### 双仓镜像与哈希

```sh
cmp openpilot/selfdrive/bigmodeld/frame_codec.h "$PHONE_ROOT/app/app/src/main/cpp/frame_codec.h"
cmp openpilot/selfdrive/bigmodeld/frame_codec.cpp "$PHONE_ROOT/app/app/src/main/cpp/frame_codec.cpp"
shasum -a 256 openpilot/selfdrive/bigmodeld/frame_codec.h openpilot/selfdrive/bigmodeld/frame_codec.cpp
shasum -a 256 "$PHONE_ROOT"/app/tests/fixtures/{frame_hevc,reply,err,hello}_golden.bin
```

两份 codec 镜像一致。最终 codec SHA-256：

- frame_codec.h：`8db66faa72f3de635293259bef232b82d90a275f858031ddd57037118e923e2e`
- frame_codec.cpp：`a0747377b96388c4ea371b92ecb6e9d730952e888844f2e3de1dacc713373c45`

本次 golden SHA-256：

- FRAME：`c97802c5dde364256dc852f5161807e5a2f10949fa0f8c3169bf490fe070d19c`
- REPLY：`7f22ff249437be70a321a055cdd436ee15dbd2af175521248f1f4f8d91cb6635`
- ERR：`931defd25b0c921b9c7131b13b3dfda536f2c2780c55c151e311376dd1e8e790`
- HELLO：`2091b0358406f237481cfb6a4a0b58047d5396cccfb9faa2903efb61e317c56b`

手机仓负责 Python 协议镜像/golden 生成；本车机实现直接使用其 v4 golden 互解，没有跨仓写入。

## 未通过/未执行的门槛

1. 完整构建：直接执行已有 `.venv/bin/scons -j8` 因该入口遗留的解释器路径失效退出 127；改用已有 Python 执行 `"$PYTHON_BIN" -m SCons -j8`，退出 2，提示 **No tool module 'cython' found**。本树 msgq/rednose 等子模块未初始化。没有重试同一障碍、安装依赖、初始化远端子模块或绕过构建。Mac 宿主也无法证明 comma_arm64 的 main.cc/cereal C++ 生成/实际 bigmodeld 链接；此门仍开放。
2. 渲染集成：执行 `LOG_ROOT=/tmp/phone-nav-logs PARAMS_ROOT=/tmp/phone-nav-params "$PYTHON_BIN" -m pytest openpilot/selfdrive/ui/tests/test_nav_card.py openpilot/selfdrive/ui/mici/tests/test_nav_card_render.py -q`，收集阶段退出 2，**ModuleNotFoundError: pyray**。没有安装或用伪造模块冒充通过。渲染 fixture、健康位与长卡宽度断言已更新，但这份渲染套件未执行；只完成纯格式化、Python 编译和 HTML 状态自检。
3. 真设备：按简报禁止操作设备，未探测、连接、部署或运行车机/手机。真车必须联合验收：单弯/连续弯/U 形路、2 Hz 更新间隙补偿误差、同目标保持/删除/降速、手机定位与 motion 失效清零、倒车、时钟休眠、网络排队，以及 v3/v4 两方向实际 socket 配对拒绝。宿主前 16 B 错版测试和 golden 不能替代它。
4. 没有将测试通过表述成完整项目构建、UI 真机布局或真实纵向舒适性验收。双端必须成套发布，本次没有发布。

## 交付

未提交代码、README、共用 JSON fixture、测试、此完整验证记录及可运行 HTML 留在指定工作树。未增加兼容层、GNSS fallback、功能开关、新消息服务或通用导航框架。审查后仍需在具备依赖/arm64 工具链的验证环境完成构建和渲染套件，并在取得设备授权后执行联合验收。
