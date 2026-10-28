# LanLink Sienna Patched LTA 激活

共享 Toyota DBC 明文继续保留。本功能只对受支持的运行路径施加激活门槛，不提供 DBC 内容保密性，也不抵抗修改程序绕过授权。

车辆选项页的 Sienna Patched LTA 卡片输入激活码，点击「激活并下载」。后端使用 `HARDWARE.get_serial()` 的硬件只读序列号，与激活码及 `device_role=headunit` 一起提交固定 HTTPS origin 的 `/api/activate`；同一激活码可另绑一台 Chipmunk OS 手机，车机与手机的绑定和当前令牌互不影响。序列号读取失败拒绝激活，不生成 UUID，不以 Params 替代。绑定其他车机时保留上游 409 语义。只有停车时可激活或 provision，激活期间切到 onroad 会终止安装。

实际 `DBC(name)` 加载也强制执行设备绑定：`ProtectedDBCStore.load()` 在解包前核对 state.device_id 与本次读取的硬件序列号。复制包含 state、设备密钥、包装 release key 和密文的完整目录到另一序列号车机，仍拒绝加载。序列号缺失、读取异常或默认 PC provider 均拒绝；测试通过显式 serial provider 或测试 DBC adapter 运行，不提供生产跳过检查的选项。

车机原生 platform_selector 与 LanLink 使用同一选车门禁；通用 Params 写接口不允许直接写 CarPlatformBundle。已选车型在吊销、缺包或版本变化后不可用时，card 在同一进程中等待恢复，不发布可用 CarParams、不发送 CAN 控制帧，也不回退到其他车型或共享明文。

本地接口：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | /api/vehicle/sienna-lta | activated、ready、revoked、cleanup_pending、can_activate、source_sha；不返回令牌、密钥或激活码 |
| POST | /api/vehicle/sienna-lta/activate | JSON `{code}`；成功返回公开状态，失败固定错误文本 |
| POST | /api/vehicle/select | 原 `{name}` 契约；仅 Patched LTA 要求 ready，未激活/未 provision 返回 403，待清理返回 409 |

其他 Sienna、RAV4 与其他车型照常选择。卡片使用 password 输入、关联 label/说明、提交期间禁用和可访问的状态/错误提示；激活码仅保留于当前页面内存，未写入 Params、localStorage 或日志。既有 LanLink 为局域网单用户明文 HTTP，激活码从浏览器到设备沿用该传输方式；只有设备到 toyochip/R2 的请求使用固定 HTTPS。

非 Toyota 平台隐藏此卡片；无车与 Toyota 场景仍可用。`issue=device_mismatch` 明确提示绑定设备不符，`serial_unavailable` 包括硬件读取 KeyError，`source_unavailable` 表示当前源码标记不可用；这些状态不会误报为即将自动下载。成功轮询会清除前一次读取错误。

设备资源根目录 `/data/dbc/sienna-patched-lta/` 为 0700。state.json 与其他资源文件为 0600，原子替换并 fsync。release key 只在内存使用并以独立随机设备密钥 AES-GCM 包装保存；manifest 原字节作为包装 AAD。AGNOS 没有硬件密钥库：软件包装只能减少意外泄漏，**不能抵抗 root**。激活令牌只在该私有目录保存，不进 Params/日志。激活码不落盘，重新激活时重新输入。

`dbc_monitor` 是 manager 的 always_run 进程，独立于 lanlinkd 与 LanLinkEnabled。启动即检查授权，之后每 300 秒联网检查；每 5 秒检查本地待清理状态。源码版本变化会重建资源上下文。状态已完整验证后，仅缓存文件签名对应的 readiness，不缓存明文资产；文件、安装、吊销、清理或 source 变化会使 readiness 失效。源码/存储异常时后台继续重试，车型保持不可选。

SOURCE_SHA 丢失或损坏只使 readiness/provision 不可用，不阻断 token-status 或持久化 pending 清理。所有远端请求在 flock 外进行；锁仅保护本地状态、安装和清理。收到 410 后复核当前 token，安装前复核完整状态，避免旧响应删除新激活或下载在吊销后重新落盘。同一 LanLink 实例拒绝并发兑换，状态页不会排在慢网络后等待。

吊销必须同时满足：固定 `https://tp.diaperastiko.top/api/token-status`、正常 TLS 校验、无重定向、HTTP 410、application/json、JSON 为 `{"status":"revoked"}`。HTTP 200/401/429/503、断网、错误 origin、重定向、HTML、错误 JSON 一律不触发删除。下载/密钥接口的失败也不触发吊销删除；客户端不打印响应体或异常中的秘密。

确认吊销后先持久化 revoked=true、cleanup_pending=true。onroad 只保留该标记，拒绝新激活和新 DBC 调用，不清文件、不干预正在使用的对象。offroad 才幂等清理整个专用目录中除 state.json/lock 以外的资源：所有 source 的密文、manifest、包装 release key、设备密钥、current、下载状态，以及将来可能留下的 plaintext/overlay/cache 和临时文件；最后持久化无 token、revoked=true、cleanup_pending=false。清理中任何一步失败或断电，pending 保留，重启/断网仍能继续清理。清理不会删除共享 Toyota DBC 或其他车型的设置。

受保护 DBC 对象和解密内容不保存在全局缓存；每次新 `DBC(name)` 从受保护存储认证读取并检查持久化状态。因此没有需跨进程失效的受保护 DBC 缓存；onroad 已有调用者的对象由驾驶进程停车退出时释放，不能承诺 Python 内存的物理擦除。

清理完成后允许重新激活；原激活码能否再次使用由服务器决定。服务器已发令牌但车辆随后切到 onroad 时，保存令牌并返回 202 的已激活/尚未就绪状态，延后 provision，停车后无需再次兑换。激活成功但下载失败也保留令牌。下载源、离线旧包选择与回滚契约见 [发布文档](../../../tools/release/DBC_PACKAGES.md)。

真机验收仍需覆盖：真实序列号、LanLinkEnabled=false 下后台存活、onroad 吊销不删文件、offroad 清理、断电续清、离线保留、重新激活、源码回滚与原 Sienna/RAV4 CAN 一致性。本次未连接生产服务。
