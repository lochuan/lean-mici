# bigmodeld —— C4 上行进程 + 双路硬编（16 号）

生命周期跟随 modeld（`system/manager/process_config.py` 注册 `only_onroad`）：从 camerad 的
VisionIPC 取 road（`VISION_STREAM_NARROW_ROAD`）/wide（`VISION_STREAM_WIDE_ROAD`）两路帧，
在取帧线程按标定矩阵 warp 成模型视图 512×256 NV12（App 只做 pack），
用 `logger_lib` 的 `V4LEncoder` 双路硬编（HEVC Main、VBR、无 B 帧、GOP 20、venus realtime 优先级、
默认每路 2 Mb/s 可调），按 10 号布局组 FRAME 经 TCP 发出（road 出包即发）。不落盘、不进 loggerd（编码输出
只进发送器，不发布编码数据服务）。`frame_codec.{h,cpp}` 与 chipmunk App 的 `frame_codec.h` 为同一线协议（BGM1 v4，见下「线协议 v4」）的两份镜像，须逐字节同源，改一边须同步另一边。

## 模块

| 文件 | 职责 |
|---|---|
| `frame_codec.{h,cpp}` | BGM1 线协议 FRAME/REPLY/ERR 编解码（与 chipmunk `app/app/src/main/cpp/` 原样同源，勿改；布局表见其头注释） |
| `warp_pack.{h,cpp}` | NEON warp：`warpNv12` 相机帧 → 512×256 NV12（协议 v3 在 C4 上 warp，手机只做 pack） |
| `frame_meta.{h,cpp}` | 帧头元数据：warp 矩阵 C++ 复刻 + MetaProvider（标定喂 rpyCalib，modeld 喂 desire/action_t，calib 线程只喂 deviceMotion；MapTargetWriter 使用手机位置健康位与沿路线距离） |
| `frame_scheduler.{h,cpp}` | I 帧/丢帧状态机（纯逻辑事件输出，调用方执行 request_keyframe） |
| `meta_cache.h` | 编码输出查表（FIFO/路）：miss 分类（stale 静默 / gap=断档上报），查不到不弹队 |
| `uplink_sender.{h,cpp}` | 发送/重连状态机（可注入 socket/时钟） |
| `frame_stages.h` | C4 本机分段计时（取帧/warp/配对/硬编/发送/等 REPLY，按 frame_idx 环形槽），随 bigModelReply 上报 |
| `main.cc` | 进程组装：取帧/配对、V4L 编码、发送、REPLY 接收、标定线程 |
| `gen_warp_golden.py` | 矩阵 golden 表生成（宿主测试对照 Python ≤1e-5） |
| `make_warp_golden.py` · `fixtures/` | warp 对 tinygrad `make_frame_prepare` 的 golden 生成与数据（`test_warp_golden.cc` 对照） |
| `server_locator.{h,cpp}` | 服务端定位（06 号）：手动 IP / 限 wlan0 子网的 mDNS 发现（avahi-browse） |
| `msg_framer.h` | 下行定长消息分帧（HELLO 48 / REPLY 8480 / ERR 40 B，REPLY 被 TCP 拆包时按累计长度拼齐） |
| `nav_outputs.{h,cpp}` | NavExt 的车机侧输出（纯逻辑）：REPLY → `navStateSP` 快照（弯道不含经纬度）；CURVE + GNSS → `/dev/shm/params`（`MapTargetWriter`，≤2 Hz、安全清空） |
| `test_bigmodeld.cc` | 宿主单测（无设备依赖，Mac/Linux 直接编译） |

## 实现口径

1. **编码前配对**：两路 VisionIPC 线程收帧→`warpNv12` 写进 512×256 编码缓冲（venus 对齐、
   `VENUS_BUFFER_SIZE`，21 号 EINVAL 坑；取帧线程 road→core 2、wide→core 6（避开 core 4/5 上 FIFO 53 的 control/ui 与 core 7 上 FIFO 54 的 modeld）、FIFO 50，warp ~2.3 ms/路）→按 `timestamp_sof` 邻近配对
   （单槽/流，10 ms 容差）→两路同时 `encode_frame`。未配对死亡帧只计数/`LOGW` 并归还缓冲，
   不进编码器、不上报调度器；缺半帧不会中断码流。
   拷贝后的 VisionIPC 缓冲立即可复用（配对等待不占上游缓冲）。
2. **I 帧事件落点**：request 在帧**提交前**发出（21 号实测「下一帧立即 I 帧」，`RESULTS.md:11`），
   落点=事件后第一个提交帧=新序列第 0 帧；seq 位置 10 给 wide 补 I（on_frame_submit 内发出，
   落本帧）。IDR 预测= request 落点 ∪ GOP20 基准（road 0,20,40,…；wide 0,10,30,50,…，与
   12 号 x265 素材分布一致）→ 即 FRAME 头 flags。
   事件回调（sender 锁内）只入队，请求统一累积、在下一次组帧的 encode_frame 前 flush
   （落点不变，且保证两路编码器已就绪）。
    触发新序列与双路 `request_keyframe()` 的只有码流断档：发送侧显式丢弃（`kDrop`，
    队列覆盖、段长超限）、编码输出缺帧（MetaCache `kGapMiss`，见 9）；假死截断/重连走
    `kNewConnection` 重置连接状态并新开序列。配对杀帧只计数/日志，时间槽空洞不触发恢复；
    GOP 相位按实际提交帧数推进。`kHeadBarrier`（序列头门丢弃）虽非断档，也落
    `on_frame_dropped` 起新序列 + 双路 request（语义见 `frame_scheduler.h`）。
3. **发送**：submit_road 即组 chunk1（头 168 B‖road 段，flags bit0=road 段 IDR、
   bit1=wide 段 IDR）先发（road 出包即发）；submit_wide 组 chunk2（wide_len‖wide‖尾部保留 16 B 零）。槽：在途 1 + 排队 1
   （只留最新；新包覆盖=丢旧包报 kDrop；旧 frame_idx 迟到包静默丢）。
   **序列头门（18 号 #1/#2）**：建连/重连/断档后第一个发出的帧必须是「预测双 IDR +
   实测双 IDR」对，不合格整对丢弃报 `kHeadBarrier`（调用方补 request），头对在排队槽
   等 wide 实测；断档后首帧恒为双路 IDR 对，服务端判据简化为「中流双路 IDR = 新序列」。
   **连接代号（18 号 #2）**：`conn_epoch` 每次建连 +1 随 `kNewConnection` 带出，
    submit_* 带调用方代号、不符即静默拒——重连窗口旧连接的
   编码在途输出不得混进新连接（污染 frame_idx 编号）。假死=对在途帧 ≥200 ms
   无进展（写阻塞或 wide 缺失）→ 尝试发完在途帧（含连续部分写，不撕裂）否则截断 → 重连，
   报 kStall。重连每次 connect 1 s 超时，连败 3 次报 kLinkLost，之后持续重试、成功复位；
   建连/重连报 kNewConnection，重连清空队列与编码在途（旧帧不带进新连接）。建连在锁外做
   （submit_* 未连接即丢，不阻塞编码回调）。段长超协议上限（1 MiB，服务端判 BAD_FRAME）
   的帧死亡不进发送路径（丢帧上报 + 新序列）。
4. **meta**：warp = K_cam @ V @ R(rpy) @ inv(K_model @ V)；
   V = view_frame_from_device_frame = [[0,1,0],[0,0,1],[1,0,0]]（`camera.py:65-66`
   device_frame_from_view_frame.T）；R = rot_from_euler（Z-Y-X，Rz@Ry@Rx）。
   road 用 medmodel K（910,0,256;0,910,47.6，bigmodel_frame=false）、wide 用 sbig
   （455,0,256;0,455,151.8，true）（`model.py:65-70` + `modeld.py:326-333`）。内参常量=
   os04c10（`camera.py:52` _os_config）：narrow fl=1141.5、wide fl=425.25、1344×760→cx 672/
   cy 380——与 frame_meta.cpp 常量逐位一致（gen_warp_golden.py 尾部注释对读）。
   golden 口径：rpy 按 float32 取值再以 double 精度算同公式；**modeld 的 float32 中间路径
   另带 ~2.7e-5 噪声（相对 ~1e-7），实测超 1e-5 判据，故 golden 走 double 路径**，判据 ≤1e-5。
5. **编码**：EncoderInfo 自带 get_settings（FULL_H_E_V_C、bitrate 初值 = `--bitrate`/
   `kDefaultBitrate` 默认 2'000'000/路、gop 20、
   b_frames 0），`V4LEncoder::Options.output_callback`（1a 接缝，`encoder.h:25-33`、
   `v4l_encoder.h:16-24`）；回调里 IDR AU = header(VPS/SPS/PPS)‖dat（仅实际 keyframe 时拼，
   `v4l_encoder.cc:120-133` header 每帧都给）。不设 PubMaster ⇒ loggerd 无编码数据
   （checklist 项自动满足）。可调码率：Params "BigmodelEncoderBitrate"（仿
   `encoderd.cc:54-60` 每帧读）+ `--bitrate` 初值。
6. **线程/锁序**（`main.cc` 头注）：2 取帧/配对（state_mtx）+ writer（sender.step）+
    REPLY reader（`msg_framer.h` 分帧 → parse_reply/parse_err，每条 REPLY 发 `navStateSP`
   并喂 `MapTargetWriter`；EOF/ERR → notify_disconnect）+ 标定
   （SubMaster(["extrinsicsCalibration"]) → MetaProvider.set_rpy）。`MapTargetWriter` 只在 reply
   线程做决策；mem Params 的**阻塞** put/remove 全在非实时的 calib_thread（它本来就按 20 Hz 被
   节拍源唤醒，未设 SCHED_FIFO/绑核）或所有线程 join 后的退出路径，reply 回包路径不做文件 I/O。
   锁序 sender.mtx > state_mtx > 叶子锁（ev 队列/meta 缓存/buf 池）。sender 事件回调在其
   锁内只记账+入队，状态机转移与 request_keyframe 在下一次组帧前按序执行（落点不变且
   不反向取锁）。SCHED_FIFO 53 + 绑核 EINVAL 容忍（`util::set_realtime_priority`/
   `set_core_affinity`，仿 `encoderd.cc:214-216`）。writer/reply 空闲只等事件（提交/断连唤醒 writer，
   REPLY 可读或 socket shutdown 唤醒 reply），100 ms 超时只作退出检查；未连接与写阻塞仍 2 ms 重试（90 号）。
    `frame_idx = (road.timestamp_sof - sof_base + 25 ms) / 50 ms`（整数除），`sof_base` 是
    `kNewConnection` 后第一个被编号 road SOF（组帧或 road 配对杀帧，先到为准）；重连时重置。
    标称 50 ms 间隔编号 +1，100 ms 空槽编号 +2；相机缺帧/配对失败会留下时间槽空洞，属于
    只记账的常态，不是码流断档。只有发送侧显式 `kDrop` 与假死截断/重连触发序列恢复。
7. **meta 缓存（18 号 #4+#5 根因修复）**：编码输出回调按各路 frame_id 从 MetaCache
   （每路 FIFO）取提交上下文。**查不到绝不弹队**（旧实现弹空全队，clean2 轮把新序列
   双路 IDR 对吃掉）；miss 分类 `kStaleMiss`（旧连接/已清/已弹的迟到输出，静默）vs
   `kGapMiss`（输出被吞=码流断档，上报新序列 + `notify_stream_gap()` 关门），
   命中前缀死条目同断档一并上报。口径详见 `meta_cache.h` 头注释。
8. desire[8]/action_t[2] 由 bigmodeld 订阅 msgq 填入 FRAME 头；REPLY 的 outputs[0:2066) 与遥测经 msgq 转交 modeld。
   连接（06 号）：连接建立后服务端先发 HELLO（ver + instance_id + max_frame，无鉴权——
   05 号握手/HMAC/配对已随 ADR-0003 撤销）；`instance_id` 区分服务端重启/网络闪断，
   状态串写 Param `BigmodelLinkState`（07 号读）。连接目标 = 手动 IP（Params
   `BigmodelServerHost` 非空）或 mDNS 自动发现（限 wlan0 子网，见 `server_locator.h`）。

## 线协议 v4（BGM1，`kVersion = 4`）

两端锁步升级，不兼容 v3。所有消息先从前 16 B 验证版本，再解释 payload。
手机拒绝旧 FRAME 并返回 `ERR VERSION_MISMATCH`，`detail=(peer<<8)|4`；
车机 `MsgFramer` 收到旧 HELLO/REPLY/ERR 头即拒绝并断开，清空导航缓存和弯道目标。
`BigmodelLinkState=version_mismatch` 保持到正确 HELLO，后续 connecting/stall/lost 不覆盖。
权威逐字段布局在两仓相同的 `frame_codec.h/.cpp`。

| 消息 | v4 大小与字段 |
|---|---|
| FRAME | 固定头 **152 B**；线长 **172 + road_len + wide_len** |
| VEHICLE | speed u16@144、yaw i16@146、flags u8@148、reserved 3 B@149 |
| REPLY | **8480 B**，len=8440；NavExt 仍 @8304 / 160 B |
| HELLO / ERR | 48 / 40 B，ver=4 |

FRAME 不含车机 GNSS 的坐标、航向、误差、fix age 或有效位；bigmodeld 不订阅
`gpsLocationExternal`，不生产或读取 `LastGPSPosition`。其它 openpilot 功能的 GNSS 不在此链路。
车辆速度/yaw 仍来自 `deviceMotion`，按 `rot_from_euler(rpyCalib)^T` 转入车体系：
前向速度取绝对值，饱和 65535 cm/s；yaw 取 z，右转为正，饱和 ±32767（INT16_MIN 无效）。
flags bit0 yaw_valid、bit1 reverse、bit2 standstill、bit3 speed_valid；reserved 恒零。
保留 100 ms 源新鲜度，speed 还要求 inputsOK/posenetOK，二者都要求 event valid、sensorsOK、
标定非 invalid 和有限分量。speed 无效时速度/reverse/standstill 清零，yaw 独立有效。

NavExt 的 bit0 改为 `phone_location_ok`，capnp 同 ordinal 改为 `phoneLocationOk`；
bit1 route_ok、bit2 nav_ok、bit3 rerouting 不变。`NAV_STATE.age_ms` 在手机位置有效时是
**支撑本状态的手机采样年龄**，不因 motion tick 续期；不可用时清 bit0 并按状态发布计龄。
`CURVE.age_ms` 独立表示距离计算结果年龄；正年龄向上取整到毫秒，0xFFFF 表示未知。
`navStateSP` 保持 20 Hz / qlog decimation 10，不含经纬度。

### SCC-map 距离 envelope

保留 `NavLinkSession → MapTargetWriter → mem Params → SmartCruiseControlMap`，无新增服务。
`MapTargetVelocities=[]` 清空；非空使用以下 object，旧非空列表一律拒绝：

```json
{"sample_time_ns":9850000000,"valid_until_ns":10450000000,"points":[{"latitude":37.422,"longitude":-122.084,"distance_m":120,"velocity":11.111}]}
```

时间为 **C4 本机 BOOTTIME 的保守推定值**：`sample=t_eof-CURVE.age_ms`，
`valid_until=min(sample+2s,t_eof-NAV_STATE.age_ms+2s)`。t_eof 早于手机收齐本帧，
允许提前过期，禁止以 REPLY 到达时刻续命。两种 age 分别检查未知、下溢、未来和期限。
NAV_STATE/CURVE 必须 present、phone_location_ok，沿路线点还需 route_ok 且非 rerouting。
点中的坐标来自离线目标几何，只作同目标保持/删除/降速比较，不计算车机到目标的地理距离。

新增/提速/距离变化最多 2 Hz，不变 1 Hz 刷新；失效、删点、降速下一拍写。
reply 线程只交付待写值，非实时 calib 线程执行 Params I/O。消费者验证最多 8 个点、
0..600 m 单调距离、有限正速度和合法目标坐标，坏数据清目标并限流记录原因。
消费者先按当前车速扣除 sample→receive 的短延迟，再在现有控制节拍累计前进距离；
`distance=max(0,snapshot.distance_m-travelled)`。重复快照不重置累计、不能延长期限，
旧快照、倒车、carState 无效、非有限/负速度、时间倒退和超期立即清目标。
C4 显式用 `CLOCK_BOOTTIME`，Darwin 宿主测试使用注入时钟。
保留既有 TARGET_JERK/ACCEL/OFFSET、override 状态机和同坐标目标保持。

连接代次变化、断连、ERR 和错版均清摘要与目标；启动/退出写 []，manager 保留
上下路及 writer-gone 清理。`LastGPSPosition` 不再属于此模块的清理键。
导航卡 positionLost 显示 `Phone location unavailable`，手机健康位失效隐藏旧转向/ETA。
可运行文案预览：[phone-location-card.html](../../../docs/phone-location-card.html)，不代表车机渲染验收。

真车联合验收仍需：单弯/连续弯与 U 形路沿路线距离、2 Hz 间隔补偿、同目标保持、
手机失效/车速失效/倒车立即清零、旧新协议互拒、休眠时钟与网络缓冲。不得单端部署。

## 构建

- **设备（comma_arm64）**：`scons -j8`（SConstruct 已注册，仅 comma_arm64 构建；
  `system/loggerd/SConscript` 已 `Export('logger_lib')` 供本目录链接）。
- CLI：`bigmodeld [--host HOST|auto] [--port PORT] [--bitrate BPS]`；`--host` 缺省读
  Params `BigmodelServerHost`（空 = mDNS 自动发现，限 wlan0 子网）。联调用
  `--host 127.0.0.1`（`adb reverse`）、2'000'000 bps/路。

## 宿主测试（Mac/Linux，无设备依赖）

```sh
cd openpilot/selfdrive/bigmodeld
clang++ -std=c++17 -O1 test_bigmodeld.cc frame_codec.cpp frame_meta.cpp \
        frame_scheduler.cpp uplink_sender.cpp server_locator.cpp nav_outputs.cpp \
        -o /tmp/test_bigmodeld
# 跨端 golden 互解（默认必跑；golden 由 chipmunk 仓库 app/tests/make_frame_golden.py 生成，*.bin 不入库）。
# 未设置目录 / 文件缺失或为空都判失败；显式跳过：BGM1_GOLDEN_SKIP=1
# （先在 chipmunk-os/app 跑 `python3 tests/make_frame_golden.py` 生成 golden；AU-16）
BGM1_GOLDEN_DIR=<chipmunk-os>/app/tests/fixtures /tmp/test_bigmodeld
# warp 查找表（需 arm64/NEON，Apple Silicon 可直接编）
clang++ -std=c++17 -O1 test_warp_lut.cc warp_pack.cpp frame_meta.cpp frame_codec.cpp \
        -o /tmp/test_warp_lut && /tmp/test_warp_lut
# warpNv12 + pack 对 tinygrad golden（m3 逐位，m1/m2 仅零星 tie 翻转）
clang++ -std=c++17 -O1 test_warp_golden.cc warp_pack.cpp -o /tmp/test_warp_golden && /tmp/test_warp_golden
```

覆盖：状态机全转移（GOP20/wide 晚 10 帧、发送侧显式丢弃恢复、配对失败/时间槽空洞不恢复、时间槽编号）、
MetaCache（查不到不弹队、clean2 回归、miss 分类）、序列头门（开门条件/整对丢弃/断档后首帧双 IDR）、
连接代号（重连窗口旧提交静默拒）、
发送器（假 socket/假时钟：出包即发、覆盖丢弃、假死发完/截断、重连、kLinkLost 升级、迟到
包静默丢、部分写不撕裂）、warp golden ≤1e-5、MetaProvider（纯 deviceMotion VEHICLE：单位换算、
100 ms/yaw_valid/speed_valid 边界、deviceMotion 车体系换算与 Python 对照、yaw 右转符号、20 Hz 采样相位
（含丢帧/迟到 60 ms）、量程饱和、fill 零堆分配）、frame_codec
往返与坏输入（含 HELLO）、LinkStateTracker（instance 对比判 blip/restart）、
下行分帧（8480 B REPLY 分两次到达、切点在 NavExt 中间；旧版本在 16 B 头处拒绝）、
navStateSP 快照（present→has*、单位换算、无效→NaN、弯道经纬度不影响输出）、
MapTargetWriter（8 点 JSON、空列表、失效/删点/降速在下一 calib 拍清空、手机失效/独立年龄过期清空、
距离 envelope、1 Hz 刷新 / 2 Hz 上限、目标 ≤2 Hz 的 20 Hz×10 s 节拍、20 Hz 调用耗时）、
NavLinkCache 断连复位、退出清理 ops、avahi 行解析/子网范围/发现节流（06 号）。
golden 表生成（输出粘进 test_bigmodeld.cc）：

```sh
/Users/kevin/Documents/Projects/1b-model-qnn/.venv/bin/python \
    openpilot/selfdrive/bigmodeld/gen_warp_golden.py
```

## 真机验证（C4 ↔ Mac 参考服务端）

C4 构建走 21 号先例 g++ 或 scons；验证：camerad 起流 → bigmodeld → `adb reverse` 连 Mac
参考服务端（复用 `android/tools/bgm1_frame.py` 解析）；验码流 PyAV 可解、I 帧位置
（road GOP20/wide 偏 10）、20 Hz ≥600 帧、卡顿注入触发丢帧+request_keyframe 恢复、
loggerd 无编码数据。C4 serial 55873f9（22 号票占用中——延后或与占用会话协调）。
