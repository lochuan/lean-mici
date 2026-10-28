# Sienna Patched LTA DBC 实现报告

日期：2026-10-28。工作区：`/Users/kevin/Documents/Projects/sunnypilot` 及其中 `opendbc_repo`。所有改动留在工作区，未暂存、提交、推送或部署。

本报告已包含 H1、M1–M5、L1–L5 返修。原生 `platform_selector.py` 与对应测试最后由主会话接手；本会话停止修改该文件，下表记录交接前已经验证的共享门禁行为与其余修复证据。

## 实现结果

- 构建器从 Patched LTA 的 DBC 依赖生成完整 pt/adas 内容，用两个平台专用名称、确定性明文与 zstd 编码、独立随机 AES-256-GCM release key、1 MiB 分块及独立 DBC domain 打包。公共目录仅两文件，九字段 key record 独立 0600 存储。
- 生产构建入口核对实际 HEAD、本地 lean-master 提交与 --source-sha，一并核对 opendbc gitlink/HEAD，并拒绝主树、子树的已跟踪修改与未跟踪文件。编码测试夹具不能跳过生产 CLI 校验。
- 运行时严格解析 manifest，拒绝重复/额外字段与错误类型；逐块认证、校验 framing、zstd 大小、明文与资产 SHA 后才释放内容。两个专用名及其路径/.dbc 别名不回退共享明文；普通 Toyota 的加载与 CAN 逻辑保持既有行为。
- provision 使用运行树 SOURCE_SHA 对应的完整 latest manifest，核对 id 下的 manifest、包 hash/source 和 release key 原字节绑定，再将 release key 用独立设备密钥包装保存。没有将激活码、令牌或明文 release key 写入 Params/日志。
- LanLink 卡片与 HTTP 接口实现硬件序列号绑定激活、409 换机冲突、就绪/吊销/待清理状态和车型选择门禁。
- 实际 DBC 加载接缝每次读取硬件序列号并与 state.device_id 比对，拒绝复制完整资源目录后的跨设备加载，以及序列号未知/读取失败的加载；默认 PC provider 也拒绝。LanLink 与加载器共用同一硬件 provider，测试通过显式注入运行。
- manager 独立 always_run 的 dbc_monitor 不受 LanLinkEnabled 控制；启动及每 300 秒联网检查，每 5 秒续清。只有固定 HTTPS origin、无重定向的 410 JSON revoked 触发持久化吊销。行驶中只标 pending；停车清理，断电/断网后能续清并重新激活。
- 不建立受保护明文/DBC 对象全局缓存；readiness 只缓存已验证文件的签名，安装、文件、source、吊销与清理变化使其失效。清理覆盖整个专用资源根目录，保留状态与锁，移除令牌及全部资产/密钥/下载/overlay/cache/临时残留。
- 车机原生选车与 LanLink 使用共享 ready 门禁，通用 Params 接口禁止写 CarPlatformBundle。已选 LTA 因吊销、缺包、源码变化或序列号异常不可用时，card 进程等待恢复，不发布可用 CarParams、不发送控制帧、不回退到其他平台或明文。
- 本地按当前 revision 查找并完整认证 releases 中的有效包，current 仅是优先指针，支持 A→B→离线回滚 A。
- SOURCE_SHA 缺失或损坏不阻断 token-status/cleanup_pending；只有 readiness 与 provision 降级。全部网络 IO 移到 flock 外，提交时复核状态/token；同一进程的不同资源上下文也不能并发兑换激活码。服务器发 token 后切到 onroad，保存 token 并返回 202，停车后延后 provision。
- stage 写入真实 SOURCE_SHA 并核对发布 manifest；仅移除测试 adapter 及四个引用它的受保护测试文件，保留 tests/car_diff.py 等运行时依赖。门禁额外拒绝 adapter 残留，组装测试验证 diff_report 所需导出。Cabana 使用明确的开发映射指向现有共享源 DBC，不改变生产运行时入口。

## 文件清单

相对 sunnypilot 根目录：

| 文件 | 用途 |
| --- | --- |
| tools/release/dbc_package.py | 加密包构建 CLI |
| tools/release/test_dbc_package.py | 原内容逐字一致、错 key/篡改/截断/尾随拒绝、私有路径、随机 key 与临时 Git 源一致性检查 |
| tools/release/testdata/dbc_package.py | 明确的 manifest 编码测试 adapter；生产构建器不使用 |
| tools/release/device_release.sh | SOURCE_SHA 写入、仅剥除 adapter 与指定受保护测试文件 |
| tools/release/release_lib.py | SOURCE_SHA/manifest 一致性与 adapter 发布门禁 |
| tools/release/test_release_lib.py | stage 原有校验及新门禁/组装回归 |
| openpilot/system/lanlinkd/dbc_resources.py | 网络、provision、设备绑定与持久化清理生命周期 |
| openpilot/system/lanlinkd/dbc_monitor.py | 独立后台检查与重试 |
| openpilot/system/manager/process_config.py | always_run 注册 |
| openpilot/system/lanlinkd/vehicle_api.py | Patched LTA 选择门禁与 protected_choices |
| openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/platform_selector.py | 原生 UI `_set_platform()` 在写 bundle 前调用共享选车门禁，失败显示弹窗；最终收尾由主会话负责 |
| openpilot/selfdrive/car/card.py | 原生 `Car()` 初始化等待受保护 DBC，包含 get_car/CANDefine 初始化期间被吊销的竞态处理 |
| openpilot/selfdrive/car/tests/test_car_interfaces.py | 车机接口测试显式接入 DBC adapter |
| openpilot/tools/cabana/dbc/generate_dbc_json.py | Cabana 专用开发源映射，避免生成不存在的专用 DBC 文件名 |
| openpilot/system/lanlinkd/params_api.py | 禁止通用接口直接写 CarPlatformBundle |
| openpilot/system/lanlinkd/lanlinkd.py | 状态/激活 HTTP 路由，阻塞工作在线程执行 |
| openpilot/system/lanlinkd/tests/test_dbc_activation.py | 设备绑定、整目录复制后直接 DBC 加载拒绝、410-only、错误保留、断电续清、重新激活、source/hash/key 绑定、缓存失效与后台生命周期 |
| openpilot/system/lanlinkd/tests/test_dbc_card.py | 真实 Car() 的六种阻塞场景，以及原生选择方法通过显示 adapter 的 ready/未 provision/pending 回归 |
| openpilot/system/lanlinkd/tests/test_dbc_tools.py | Cabana 显式开发映射回归 |
| openpilot/system/lanlinkd/tests/test_params_api.py | 通用 Params 写入不能绕过选车门禁 |
| openpilot/system/lanlinkd/tests/test_lanlinkd_app.py | 真实 HTTP 激活/状态/行驶中拒绝及原路由回归 |
| openpilot/system/lanlinkd/tests/conftest.py | 无本机 native msgq 时的显式测试导入 adapter，实际 IPC 调用会失败 |
| openpilot/system/lanlinkd/web/src/components/SiennaActivationCard.vue | 可访问的激活码输入与状态 |
| openpilot/system/lanlinkd/web/src/components/VehiclePanel.vue | 卡片、轮询、激活及车型禁选装配 |
| openpilot/system/lanlinkd/web/src/lib/api.ts | 前端状态/激活接口 |
| openpilot/system/lanlinkd/web/src/lib/schema.ts | 状态与受保护车型类型 |
| openpilot/system/lanlinkd/web/tests/siennaActivation.test.ts | 实际 Vue 渲染与 POST body 契约 |
| openpilot/system/lanlinkd/static/index.html、static/assets/index-Ch3i_J02.js | 当前 Vue 静态产物；旧 JS 已清理 |
| uv.lock | 同步 opendbc 的 zstandard 运行时依赖元数据 |
| RELEASES.md、tools/release/README.md | 发布说明及入口 |
| tools/release/DBC_PACKAGES.md | 完整格式、R2/SOURCE_SHA/回滚与验证说明 |
| openpilot/system/lanlinkd/DBC_ACTIVATION.md | API、隐私、后台生命周期与吊销语义 |
| tools/release/DBC_IMPLEMENTATION_REPORT.md | 本报告 |

相对 opendbc_repo：

| 文件 | 用途 |
| --- | --- |
| opendbc/can/dbc_asset.py | 严格格式、分块认证解密、私有存储与设备密钥包装 |
| opendbc/can/dbc.py | DBC(name) 的专用保护入口，普通 DBC 保持缓存 |
| opendbc/can/tests/test_dbc_asset.py | 无回退、路径别名、独立跨块 wire encoder、manifest/明文完整性与 zstd 拒绝检查 |
| opendbc/car/toyota/values.py | Patched LTA 的两个专用 DBC 名 |
| opendbc/car/tests/dbc_adapter.py | 显式测试 adapter，生产不导入，stage 不携带 |
| opendbc/car/tests/test_car_interfaces.py | 通用接口测试显式使用 adapter |
| opendbc/car/toyota/tests/test_toyota.py | Toyota/Sienna 测试显式使用 adapter |
| opendbc/safety/tests/test_toyota.py | Toyota safety 的 controller 回归显式使用 adapter |
| pyproject.toml、uv.lock | zstandard 从测试依赖提升至运行时依赖 |
| RELEASES.md | 变更记录 |

## 验证结果

- 构建包、发布 gate、DBC 资产、LanLink 资源、实际 card、Cabana、车辆选择与 Params 聚焦测试：129 项通过，另 2 个子测试通过；命令与日志见下文。
- 绑定漏洞的新增七项检查先复现失败再转绿：完整目录复制后 pt/adas 的直接 `DBC(name)` 加载拒绝；空/缺失序列号拒绝；默认 provider 在匹配车机上成功，在不同序列号或 PC 上拒绝。既有 UI/status 检查没有参与这些直接加载断言。
- 真实 Sanic HTTP 测试：34 项通过；使用本机临时端口，所有远程激活/下载使用 fake adapter。存在现有 Sanic/websockets 弃用提示。
- 用户补充指定的 `opendbc/car/tests`、`opendbc/car/toyota/tests`、`opendbc/safety/tests`：1204 项通过、465 项按既有条件跳过、3054 个子测试通过。指定 safety controller driver override 回归单独通过。
- MISRA 首轮因 uv 默认全局缓存不可写导致三个子测试失败；改用 `/tmp/sienna-uv-cache` 与 `UV_OFFLINE=true`，复用项目已有依赖重跑后全部通过，没有省略 MISRA 检查。
- Sienna/RAV4 的 `openpilot/selfdrive/car/tests/test_car_interfaces.py` 聚焦 10 项通过，40 项未选择。运行真实 native Params，只通过 `/tmp/sienna_interface_host.py` 将测试路径隔离到 `/tmp`，没有设置 HOME 或改全局环境。
- Vue 实际渲染/API 聚焦测试 5 项通过；vue-tsc 类型检查通过；LanLink 前端目标构建通过。
- 浏览器 DOM 实测本地预览：激活成功后清空输入；读取失败后恢复成功的旧错误残留先在旧产物复现，更新产物后确认清除。没有截图或读取图片。
- 包、存储、资源、发布及 LanLink 的生产 Python 模块 ty 检查、shell 语法、两个仓库 diff whitespace 检查通过。新代码 ruff 检查通过；lanlinkd.py 的两处 ASYNC240、card.py 原有 RI 可空赋值均在 HEAD 原文件复现，未扩展修改这些既有问题。
- Graphify 对 26 个变更源文件做过 AST 增量更新，随后对最终发布 gate 与加载绑定修复文件补充更新；未重建无关大语料、文档语义或生成 bundle。该本地索引不属于发布产物。

验证依赖在 `/tmp/sienna-dbc-validation`；前端依赖在项目 node_modules，未安装全局依赖。最新日志：`/tmp/sienna-review-focused-tests.log`、`/tmp/sienna-review-related-tests.log`、`/tmp/sienna-selfdrive-interfaces-tests.log`。

## 独立审查逐条返修证据

下列用例均位于上述文件清单，覆盖实际调用链；外设适配只替换硬件、CAN/IPC、文件路径、HTTP 或 GUI 显示端。

| 项目 | 修复与测试证据 |
| --- | --- |
| H1 | `platform_selector.py::_set_platform` 调用共享 `select_platform`，失败弹窗且不写 bundle。`test_native_platform_selection_uses_the_same_activation_gate` 验证未激活、缺包装 key、cleanup_pending 拒绝，正常其他 Toyota/已就绪 LTA 可选。`test_vehicle_bundle_write_cannot_bypass_selection_gate` 覆盖通用 Params 入口。真实 `card.Car()` 的 `test_card_waits_without_can_output_when_selected_lta_has_no_resources` 覆盖未 provision、吊销、get_car/CANDefine 初始化期间吊销、source 变化/无效及异机序列号六种情况；检查无控制 CAN、无可用 CarParams、原选型不变。原生文件/测试最终由主会话接手复核。 |
| M1 | `test_offline_rollback_uses_installed_source_package_after_a_b_a`：A provision→B provision→全部网络失败，回到 A 后 status ready，pt/adas 均能直接认证加载。 |
| M2 | `test_builder_rejects_unverified_source_before_writing` 的六种临时 Git 故障：错 SHA、主树修改、未跟踪文件、子树修改、gitlink/HEAD 不同、当前 HEAD 不等于 lean-master；均拒绝且不产生公共输出或密钥。`test_clean_pinned_source_builds_with_its_actual_sha` 验证干净且 pin 一致的正例。 |
| M3 | `test_monitor_checks_revocation_and_resumes_cleanup_without_valid_source` 四种组合：SOURCE_SHA 缺失/损坏 × 已 pending/尚待 410。启动真实 monitor 的一轮后，无需有效 source 也完成授权检查/续清。 |
| M4 | `test_all_network_operations_release_the_local_state_lock` 在六类真实请求点用独立 fd 获取非阻塞 OS flock，证明网络不持有状态锁。`test_stale_410_cannot_remove_a_new_activation`、`test_download_cannot_install_after_concurrent_revocation` 验证提交前状态/token 复核；`test_parallel_activation_from_a_new_resource_context_is_rejected` 验证新上下文也不能并发兑换。 |
| M5 | 车机 selfdrive 接口显式 adapter：Sienna/RAV4 聚焦 10 项通过。`test_cabana_uses_explicit_development_source_mapping` 确认 Cabana 指向存在的共享源。`LocalStageAssemblyTests::test_stage_contains_bound_manifest_and_archive_without_package_aliases` 确认剥除 adapter、保留 car_diff.py，并按真实 diff_report 导入核对导出函数。 |
| L1 | `test_serial_read_key_error_is_a_controlled_unavailable_status`：KeyError 转受控 serial_unavailable/ready=false，激活返回 503，无裸异常。 |
| L2 | `test_copied_complete_resources_cannot_load_on_another_device` 对 pt/adas 均通过实际 DBC(name) 接缝拒绝完整目录复制；另覆盖未知序列号、默认 PC 拒绝与匹配车机成功。 |
| L3 | `test_token_issued_during_onroad_transition_is_saved_and_provisioned_later`：发 token 后变 onroad，返回 202 并持久化 token，不安装；停车后自动就绪，只兑换一次。 |
| L4 | `test_serial_mismatch_status_explicitly_reports_binding_problem` 返回 device_mismatch；Vue 测试确认显示「绑定设备不符」，不承诺自动重试下载。 |
| L5 | Vue 测试确认非 Toyota 隐藏、无车保留卡片；浏览器 DOM 先复现错误残留，再确认成功轮询清除旧 error。 |

复跑聚焦命令（项目根目录）：

```bash
PATH=/tmp/sienna-dbc-validation/bin:$PATH PYTHONPATH=.:opendbc_repo python -m pytest \
  tools/release/test_dbc_package.py tools/release/test_release_lib.py \
  opendbc_repo/opendbc/can/tests/test_dbc_asset.py \
  openpilot/system/lanlinkd/tests/test_dbc_activation.py \
  openpilot/system/lanlinkd/tests/test_vehicle_api.py \
  openpilot/system/lanlinkd/tests/test_dbc_card.py \
  openpilot/system/lanlinkd/tests/test_dbc_tools.py \
  openpilot/system/lanlinkd/tests/test_params_api.py -q
```

opendbc 相关目录离线复跑：

```bash
cd opendbc_repo
UV_CACHE_DIR=/tmp/sienna-uv-cache UV_OFFLINE=true \
PATH=/tmp/sienna-dbc-validation/bin:$PATH PYTHONPATH=. python -m pytest \
  opendbc/car/tests opendbc/car/toyota/tests opendbc/safety/tests -q
```

## UI 查看

- 运行页：<http://127.0.0.1:8769/>
- HTML：`/tmp/sienna-dbc-preview/index.html`
- 预览使用当前静态产物，所有 API 请求在页面内转成假数据；不会连接车机、R2 或生产激活服务。重开页面会重置假激活状态。

## 未做步骤与实际限制

- 未修改 toyochip-server 或 chipmunk-os；未访问生产 R2、生产激活/密钥服务，未上传包或导入密钥。
- 未暂存、提交、推送或部署；未运行全项目构建、全项目测试或全量格式化。
- 真机 `comma@10.0.0.27` SSH 超时。尚待真机验证硬件序列号、离线包加载、LanLink 关闭后的后台存活、onroad/offroad 时序、断电续清、重新激活、回滚与实际 CAN 输出。主机测试不能替代这些验收。
- AGNOS 的设备密钥是软件保存，不能抵抗 root；Python 内存也不承诺物理擦除。LanLink 浏览器到车机仍沿用既有局域网 HTTP；远端服务访问使用 HTTPS。
- 共享 Toyota DBC 明文仍保留，且与包资产内容相同：本实现只有受支持路径上的激活门槛，不能使 DBC 内容保密，也不能抵抗修改程序绕过授权。没有改变这一已确认限制。
- static/.build-hash 按现有 Git 索引契约生成。本次未暂存源码，后续正式暂存前端源码后需要运行现有前端构建工具刷新指纹；不要把旧索引指纹当作本次未提交源码的发布证明。
