# bigmodeld —— C4 上行进程 + 双路硬编（16 号）

生命周期跟随 modeld（`system/manager/process_config.py` 注册 `only_onroad`）：从 camerad 的
VisionIPC 取 road（`VISION_STREAM_NARROW_ROAD`）/wide（`VISION_STREAM_WIDE_ROAD`）两路帧，
用 `logger_lib` 的 `V4LEncoder` 双路硬编（HEVC Main、VBR、无 B 帧、GOP 20、默认每路 10 Mb/s
可调），按 10 号布局组 FRAME 经 TCP 发出（road 出包即发）。不落盘、不进 loggerd（编码输出
回调 ⇒ 不建 PubMaster ⇒ loggerd 无编码数据）。线协议见 `1b-model-qnn` 仓库
`.scratch/big-model-offload-v1/spec.md`「线协议」，`frame_codec.{h,cpp}` 与 App 服务端共用同一实现。

## 模块

| 文件 | 职责 |
|---|---|
| `frame_codec.{h,cpp}` | BGM1 线协议 FRAME/REPLY/ERR 编解码（与本仓库 `android/` 原样同源，勿改） |
| `frame_meta.{h,cpp}` | 帧头元数据：warp 矩阵 C++ 复刻 + MetaProvider（标定喂 rpyCalib） |
| `frame_scheduler.{h,cpp}` | I 帧/丢帧状态机（纯逻辑事件输出，调用方执行 request_keyframe） |
| `meta_cache.h` | 编码输出查表（FIFO/路）：miss 分类（stale 静默 / gap=断档上报），查不到不弹队 |
| `uplink_sender.{h,cpp}` | 发送/重连状态机（可注入 socket/时钟）+ ReplyTracker |
| `main.cc` | 进程组装：取帧/配对、V4L 编码、发送、REPLY 接收、标定线程 |
| `gen_warp_golden.py` | warp golden 表生成（宿主测试对照 Python ≤1e-5） |
| `test_bigmodeld.cc` | 宿主单测（无设备依赖，Mac/Linux 直接编译） |

## 实现口径

1. **编码前配对**：两路 VisionIPC 线程收帧→拷进编码缓冲（`VisionBuf::allocate(max(len, 2428928))`，
   21 号 EINVAL 坑 `prototypes/21/venus/venus_bench.cpp:344-346`）→按 `timestamp_sof` 邻近配对
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
3. **发送**：submit_road 即组 chunk1（头 160 B‖road 段，flags bit0=road 段 IDR、
   bit1=wide 段 IDR）先发（road 出包即发）；submit_wide 组 chunk2（wide_len‖wide‖MAC16 零）。槽：在途 1 + 排队 1
   （只留最新；新包覆盖=丢旧包报 kDrop；旧 frame_idx 迟到包静默丢）。
   **序列头门（18 号 #1/#2）**：建连/重连/断档后第一个发出的帧必须是「预测双 IDR +
   实测双 IDR」对，不合格整对丢弃报 `kHeadBarrier`（调用方补 request），头对在排队槽
   等 wide 实测；断档后首帧恒为双路 IDR 对，服务端判据简化为「中流双路 IDR = 新序列」。
   **连接代号（18 号 #2）**：`conn_epoch` 每次建连 +1 随 `kNewConnection` 带出，
   submit_* 带调用方代号、不符静默拒（`stale_submits` 计数）——重连窗口旧连接的
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
5. **编码**：EncoderInfo 自带 get_settings（FULL_H_E_V_C、bitrate 10'000'000、gop 20、
   b_frames 0），`V4LEncoder::Options.output_callback`（1a 接缝，`encoder.h:25-33`、
   `v4l_encoder.h:16-24`）；回调里 IDR AU = header(VPS/SPS/PPS)‖dat（仅实际 keyframe 时拼，
   `v4l_encoder.cc:120-133` header 每帧都给）。不设 PubMaster ⇒ loggerd 无编码数据
   （checklist 项自动满足）。可调码率：Params "BigmodelEncoderBitrate"（仿
   `encoderd.cc:54-60` 每帧读）+ `--bitrate` 初值。
6. **线程/锁序**（`main.cc` 头注）：2 取帧/配对（state_mtx）+ writer（sender.step）+
   REPLY reader（MsgHdr 分流 parse_reply/parse_err → ReplyTracker，EOF/ERR →
   notify_disconnect）+ 标定（SubMaster(["extrinsicsCalibration"]) → MetaProvider.set_rpy）。
   锁序 sender.mtx > state_mtx > 叶子锁（ev 队列/meta 缓存/buf 池）。sender 事件回调在其
   锁内只记账+入队，状态机转移与 request_keyframe 在下一次组帧前按序执行（落点不变且
   不反向取锁）。SCHED_FIFO 53 + 绑核 EINVAL 容忍（`util::set_realtime_priority`/
   `set_core_affinity`，仿 `encoderd.cc:214-216`）。
    `frame_idx = (road.timestamp_sof - sof_base + 25 ms) / 50 ms`（整数除），`sof_base` 是
    `kNewConnection` 后第一个被编号 road SOF（组帧或 road 配对杀帧，先到为准）；重连时重置。
    标称 50 ms 间隔编号 +1，100 ms 空槽编号 +2；相机缺帧/配对失败会留下时间槽空洞，属于
    只记账的常态，不是码流断档。只有发送侧显式 `kDrop` 与假死截断/重连触发序列恢复。
7. **meta 缓存（18 号 #4+#5 根因修复）**：编码输出回调按各路 frame_id 从 MetaCache
   （每路 FIFO）取提交上下文。**查不到绝不弹队**（旧实现弹空全队，clean2 轮把新序列
   双路 IDR 对吃掉）；miss 分类 `kStaleMiss`（旧连接/已清/已弹的迟到输出，静默）vs
   `kGapMiss`（输出被吞=码流断档，上报新序列 + `notify_stream_gap()` 关门），
   命中前缀死条目同断档一并上报。口径详见 `meta_cache.h` 头注释。
8. **TODO(04 号)**：desire[8]/action_t[2] 现置零——权威在 modeld 的 DesireHelper 与 13 号
   延迟公式，经 msgq 接线；REPLY 的 outputs[0:2066) 与遥测同样留待 msgq 转交 modeld
   （ReplyTracker 只缓存最新 + 分段统计）。
   另：BGM1 链路的 HELLO/AUTH/AUTH_OK 握手（spec.md:118-119）与 MAC 校验（05 号）不在
   本票——现阶段直连参考服务端（bgm1_frame.py），鉴权/配对归 05/06 号。

## 构建

- **设备（comma_arm64）**：`scons -j8`（SConstruct 已注册，仅 comma_arm64 构建；
  `system/loggerd/SConscript` 已 `Export('logger_lib')` 供本目录链接）。
- **快速迭代**：可走 21 号先例 g++ 直构（`prototypes/21/venus/build.sh` 样板，
  `-I/data/openpilot -I…/openpilot -I…/msgq_repo`）。
- CLI：`bigmodeld [--host HOST] [--port PORT] [--bitrate BPS]`，默认 `127.0.0.1:7070`
  （`adb reverse` 验证用）、10'000'000 bps/路。

## 宿主测试（Mac/Linux，无设备依赖）

```sh
cd openpilot/selfdrive/bigmodeld
clang++ -std=c++17 -O1 test_bigmodeld.cc frame_codec.cpp frame_meta.cpp \
        frame_scheduler.cpp uplink_sender.cpp -o /tmp/test_bigmodeld && /tmp/test_bigmodeld
```

覆盖：状态机全转移（GOP20/wide 晚 10 帧、发送侧显式丢弃恢复、配对失败/时间槽空洞不恢复、时间槽编号）、
MetaCache（查不到不弹队、clean2 回归、miss 分类）、序列头门（开门条件/整对丢弃/断档后首帧双 IDR）、
连接代号（重连窗口旧提交静默拒）、
发送器（假 socket/假时钟：出包即发、覆盖丢弃、假死发完/截断、重连、kLinkLost 升级、迟到
包静默丢、部分写不撕裂、发送段样本=写完时点/滑动窗口）、warp golden ≤1e-5、MetaProvider、ReplyTracker、frame_codec
往返与坏输入。golden 表生成（输出粘进 test_bigmodeld.cc）：

```sh
/Users/kevin/Documents/Projects/1b-model-qnn/.venv/bin/python \
    openpilot/selfdrive/bigmodeld/gen_warp_golden.py
```

## 真机验证（C4 ↔ Mac 参考服务端）

C4 构建走 21 号先例 g++ 或 scons；验证：camerad 起流 → bigmodeld → `adb reverse` 连 Mac
参考服务端（复用 `android/tools/bgm1_frame.py` 解析）；验码流 PyAV 可解、I 帧位置
（road GOP20/wide 偏 10）、20 Hz ≥600 帧、卡顿注入触发丢帧+request_keyframe 恢复、
loggerd 无编码数据。C4 serial 55873f9（22 号票占用中——延后或与占用会话协调）。
