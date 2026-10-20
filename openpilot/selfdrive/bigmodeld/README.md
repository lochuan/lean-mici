# bigmodeld —— C4 上行进程 + 双路硬编（16 号）

生命周期跟随 modeld（`system/manager/process_config.py` 注册 `only_onroad`）：从 camerad 的
VisionIPC 取 road（`VISION_STREAM_NARROW_ROAD`）/wide（`VISION_STREAM_WIDE_ROAD`）两路帧，
在取帧线程按标定矩阵 warp 成模型视图 512×256 NV12（App 只做 pack），
用 `logger_lib` 的 `V4LEncoder` 双路硬编（HEVC Main、VBR、无 B 帧、GOP 20、venus realtime 优先级、
默认每路 2 Mb/s 可调），按 10 号布局组 FRAME 经 TCP 发出（road 出包即发）。不落盘、不进 loggerd（编码输出
只进发送器，不发布编码数据服务）。`frame_codec.{h,cpp}` 与 chipmunk App 的 `frame_codec.h` 为同一线协议（BGM1 v3，见下「线协议 v3」）的两份镜像，须逐字节同源，改一边须同步另一边。

## 模块

| 文件 | 职责 |
|---|---|
| `frame_codec.{h,cpp}` | BGM1 线协议 FRAME/REPLY/ERR 编解码（与 chipmunk `app/app/src/main/cpp/` 原样同源，勿改；布局表见其头注释） |
| `warp_pack.{h,cpp}` | NEON warp：`warpNv12` 相机帧 → 512×256 NV12（协议 v3 在 C4 上 warp，手机只做 pack） |
| `frame_meta.{h,cpp}` | 帧头元数据：warp 矩阵 C++ 复刻 + MetaProvider（标定喂 rpyCalib，modeld 喂 desire/action_t，calib 线程喂 GNSS / VEHICLE；`latest_gnss()` 供 MapTargetWriter 取最近一条定位） |
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

## 线协议 v3（BGM1，`kVersion = 3`）

锁步升级、不做版本兼容：`MsgHdr.ver` 严格等于 3，不符即 `kBadVersion`。实际路径：手机收到版本不符的 FRAME 回
ERR `VERSION_MISMATCH`（code 1，`detail` = 对端 ver « 8 ‖ 本端 ver，如 `0x0203`）并断连；**本端以 HELLO 判版本**——
手机连上先发 HELLO，`parse_hello` 得 `kBadVersion` 即版本不符（日志 `got_ver/want_ver`，断连重连；
`BigmodelLinkState=version_mismatch` 在 `fail()` 之后写，一直保持到下一次 on_hello 成功；
connecting / 新连接 / kStall / kLinkLost 都不覆盖它）。v2 手机的 REPLY（len 8280 ≠ 8440）在 `reply_thread`
分帧处就以「协议头非法」断连，到不了 `parse_reply`；真版本不一致时对端 ERR 的 ver 也不同，`parse_err` 同样得
`kBadVersion`（只记日志），所以不靠 ERR.detail 判版本。`parse_reply` 本身先验 MsgHdr 再验长度，旧 8320 B REPLY
报 `kBadVersion`（宿主测试锁定）。
权威布局表在 `frame_codec.h` 头注释（两端同源），这里是摘要：

| 消息 | v2 | v3 | 变化 |
|---|---|---|---|
| FRAME 头 | 160 B | **168 B** | 偏移 144 的保留 16 B → **GNSS**；偏移 160 追加 **VEHICLE** 8 B；线长 = 168 + road + 4 + wide + 16 |
| REPLY | 8320 B（len 8280） | **8480 B（len 8440）** | 遥测之后追加定长 **NavExt** 160 B（偏移 8304），尾部保留 16 B 在 8464 |
| HELLO / ERR | 48 / 40 B | 不变 | 只有 ver=3；ERR `VERSION_MISMATCH.detail` 编码见上 |

**GNSS（FRAME 偏移 144，16 B，WGS-84）**：`latitude_deg_x1e7 i32` · `longitude_deg_x1e7 i32` · `speed_cm_per_s u16`（**`deviceMotion.velocityDevice`
经 rpyCalib 转到车体系后的前向分量绝对值**；`speed_valid=0` ⇒ 写 0，不是 0xFFFF）·
`bearing_centideg u16`（`bearing_valid=0` ⇒ 写 0）· `horizontal_accuracy_dm u16`（`0xFFFF` 未知，饱和 `0xFFFE`；≤0 视为未知）· `fix_age_and_valid_flags u16`
（bit0–11 **上次 fix 的** age_ms 饱和 4095；从未有过 fix 但 `speed_valid=1` → 4095 · bit12 has_fix = 最新消息 hasFix ∧ age ≤ 2000 · bit14 speed_valid · bit15 bearing_valid）。`speed_valid` = deviceMotion 100 ms 内 ∧ 消息 valid ∧ `sensorsOK` ∧ `inputsOK` ∧ `posenetOK` ∧ 分量有限；`has_fix=0` 时位置类字段（纬度 / 经度 / 航向 / 水平精度 / `bearing_valid`）无意义，写 0；`speed_cm_per_s` + `speed_valid` 与 `age_ms` 保留；车机完全无数据时整块 16 B 为 0；带 valid 位的字段（speed / bearing）以 valid 位为准，无效写 0，仅无 valid 位的 `horizontal_accuracy_dm` 用 `0xFFFF` 哨兵。

**VEHICLE（偏移 160，8 B）**：`yaw_rate_centideg_per_s i16`（`deviceMotion.angularVelocityDevice` 经 rpyCalib 转到车体系后的 z 分量，0.01°/s，**右转（顺时针）为正，与 bearing 同向**，`INT16_MIN` 无效；车机取 `w_body.z` 不取反）· `vehicle_state_flags u8`
（bit0 yaw_valid（deviceMotion 100 ms 内 ∧ 消息 valid ∧ `sensorsOK` ∧ 有限；`calStatus == invalid` 时置 0）· bit1 reverse（车体系前向速度 < −0.3 m/s）· bit2 standstill（车体系水平速度模长 < 0.1 m/s））· `bearing_accuracy_half_deg u8`（0.5°，255 未知 / 无 fix，饱和 254；≤0 视为未知）·
保留 4 B（pack 置 0，解析忽略）。

**NavExt（REPLY 偏移 8304，160 B）**：健康头 4（`present_flags` bit0 NAV_STATE · bit1 ROAD_ATTR · bit2 CURVE_AHEAD ·
bit3 NAV_DEBUG；`nav_health_flags` bit0 gps_ok · bit1 route_ok · bit2 nav_ok · bit3 rerouting）‖ NAV_STATE 24 ‖
ROAD_ATTR 16 ‖ CURVE_AHEAD 104（头 8 + 8 点 × 12）‖ NAV_DEBUG 8 ‖ 保留 4。`present_flags` 位为 0 的子结构全 0，
本端当「无此功能」；CURVE_AHEAD 线上 `point_count>8` 当坏块，清 `present_flags` bit2、子块整块按缺省（点要喂纵向）。各子结构字段、单位、无效值见 `frame_codec.h`。

**本进程现状（票 C 已落）**：`reply_thread` 每收一条 REPLY：

- 发 `navStateSP`（capnp `Custom.NavStateSP`，占原 `CustomReserved11` 的 id / log.capnp 槽 @137；
  `services.py` 20 Hz、decimation 10；decimation 只作用于 qlog，rlog 仍按 20 Hz 全量记录）。
  健康位、`present_flags` 对应的
  `hasNavState / hasRoadAttributes / hasCurveAhead / hasNavDebug`、NAV_STATE / ROAD_ATTR / NAV_DEBUG
  一比一镜像；`CURVE_AHEAD` 只镜像 `pointCount` / 横向加速度上限 / 前视距离 / `ageMs` 与每点的
  距离 / 目标速度 / 曲率，以及 `curve_flags` 的三位（`alongRoute / roadLookahead / smoothed`），
  **不含经纬度**（决策 17）。
- 把 CURVE_AHEAD（含点经纬度）+ `MetaProvider` 里票 B 已有的最近一条 `gpsLocationExternal` 快照写
  `/dev/shm/params`：`LastGPSPosition` = `{"latitude","longitude"}`（**按 GNSS 节拍 ≈10 Hz**，
  在 calib_thread 写），`MapTargetVelocities` = `[{"latitude","longitude","velocity"(m/s)}…]`
  （与 `smart_cruise_control/tests/test_map_controller.py` 同格式，零改 `SmartCruiseControlMap`）。
  弯道目标时机：内容变化 ≥500 ms（≤2 Hz）、内容不变 1 s 刷新、首次必写；**只要有目标点被删除
  （含清空）或容差内同一点降速在下一拍写**（≤1 个 calib 拍：正常 ≤50 ms，modeld 不发时最长约 1 s；
  SCC-map 对旧点有粘滞）。CURVE present=0 / 链路断开 / 数据过期 → `[]`；
  数据过期用合成判断 `now − (t_eof − age_ms)` ≤2 s（两个 2 s 不叠加；t_eof = REPLY 回显的帧
  `timestamp_eof`）。GNSS 无 fix、`event_valid=0`、fix >1 s（N3：GNSS 断流时不能靠 has_fix=0）、
  坐标非法或 (0,0) → 不写 `LastGPSPosition`，且 `MapTargetVelocities=[]`；
  水平精度未知或 >30 m（协议 T_acc）→ 不写 `LastGPSPosition`（逐条判，无状态）；`MapTargetVelocities`
  对精度带**迟滞**（`GpsAccuracyGate`，AU-3）：不可用 → 可用要 ≤30 m，可用 → 不可用要 >40 m 或连续
  3 条 GNSS 样本 >30 m（未知精度视同不达标），避免精度在 30 m 上下抖动时目标反复清空写回；
  计数按 GNSS 样本推进，20 Hz 的 reply 拍重复看到同一条不累加。
  AU-4：REPLY `rerouting=1` → `[]`；`curve_flags.bit0`（沿路线）为 1 而 `route_ok=0` → `[]`
  （bit1 沿当前道路前扫的点不依赖路线）；`target_speed_kph=0` 的点跳过。
  `MapTargetVelocities` 是 mem Params（不是 msgq 服务），不进 rlog。
- reply 线程的链路状态全在 `NavLinkSession`（`nav_outputs.h`，AU-1）：epoch 变化 / fail / ERR / 未连接
  都走 `on_epoch` / `on_disconnect` / `on_idle(false)`，复位 NavExt 摘要与 HELLO 标志并立即以
  `link=false` 走 writer（→ `[]`）；重连后第一条 REPLY 到达前不会再拿旧 NavExt 写回弯道点。
  main.cc 只把 `take_pending()` 交给 calib 线程（锁内只 swap）；宿主测试驱动同一个类。
  进程启动（起线程前）与退出（所有线程 join 后）都由主线程阻塞写 `MapTargetVelocities=[]` 并删除
  `LastGPSPosition`（崩溃/SIGKILL 重启也清残留）；reply / calib 线程退出循环时也各补一次清空。
  manager 在上路/下路切换时也对 mem Params 做同样清理（下路只是兜底；Darwin 不碰）；上路期间
  bigmodeld 不在运行（关开关 / 崩溃待重启 / 退出卡住）时，manager 每拍删掉这两个键（AU-5）。
- `navStateSP` 的浮点字段存 SI 值（定点换算后），整数编码字段保留线上原值（无效哨兵原样保留）；
  浮点无效值用 NaN（0 是有效值）。字段对照见 `cereal/custom.capnp` 的注释。
- HELLO `parse_hello` 得 `kBadVersion` → `BigmodelLinkState=version_mismatch`，**一直保持到下一次
  on_hello 成功**；connecting / 新连接 / kStall / kLinkLost 都不覆盖它。各解析失败日志带
  `got_ver/want_ver`（票 A 已落；fail() 在头部可信时也带）。

票 A 落编解码；票 B 让 `MetaProvider` 填真实 GNSS / VEHICLE——`calib_thread` 订阅
`gpsLocationExternal`（10 Hz）与 `deviceMotion`（20 Hz），两者**不进 poll 集合**（conflate 非阻塞取最新一条），
用户态循环仍只被原有 4 Hz + 20 Hz 节拍源唤醒。注意 msgq 每条消息都会对每个订阅线程发 SIGUSR2（与 poll
无关），这两路会让线程每秒多约 30 次**信号唤醒**。`fill()` 按 `t_eof` 与消息 `logMonoTime` 算 `age_ms` /
`has_fix` / `yaw_valid`；deviceMotion 的车体系换算照 `locationd/helpers.py` 的 `PoseCalibrator`
（`calib_from_device = rot_from_euler(rpyCalib)^T`），yaw 取 `w_body.z`，右转（顺时针）为正、与 bearing
同向。时钟时基：`nanos_since_boot()` 是 CLOCK_BOOTTIME；camerad 的 `timestamp_eof` 来自内核 SOF
时间戳 + sensor 读出时间，camerad 自己就用 `nanos_since_boot() - timestamp_eof` 算 processing_time
（`spectra.cc:1521-1530`），所以 t_eof 与 `nanos_since_boot()` 同为 CLOCK_BOOTTIME。
Python 发布者的 `logMonoTime` 是 CLOCK_MONOTONIC，两者只在
设备休眠后才会出现差值；openpilot 全局默认它们一致，本实现沿用该假设。yaw / speed 的有效率与大模型
REPLY 的迟到率绑定：REPLY 迟到（M 接近 60 ms）时，紧接着的那一帧可能因样本超过 100 ms 而判为无效；
上车时统计 `yaw_valid=0` 的占比，并和 `bigLateReplyCount` 对照。REPLY 的 NavExt 由 `parse_reply`
解析后进 `navStateSP` 与 `/dev/shm/params`（见上「本进程现状」）；`bigModelReply` 的模型输出路径不变。


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
包静默丢、部分写不撕裂）、warp golden ≤1e-5、MetaProvider（含 v3 GNSS/VEHICLE：单位换算、
age/has_fix/yaw_valid 边界、deviceMotion 车体系换算与 Python 对照、yaw 右转符号、20 Hz 采样相位
（含丢帧/迟到 60 ms）、量程饱和、fill 零堆分配）、frame_codec
往返与坏输入（含 HELLO）、LinkStateTracker（instance 对比判 blip/restart）、
下行分帧（8480 B REPLY 分两次到达、切点在 NavExt 中间；旧 v2 头拒绝）、
navStateSP 快照（present→has*、单位换算、无效→NaN、弯道经纬度不影响输出）、
MapTargetWriter（8 点 JSON、空列表、失效/删点/降速在下一 calib 拍清空、无 fix/精度不达标不写位置、
1 Hz 刷新 / 2 Hz 上限、位置 10 Hz + 目标 ≤2 Hz 的 20 Hz×10 s 节拍、20 Hz 调用耗时）、
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
