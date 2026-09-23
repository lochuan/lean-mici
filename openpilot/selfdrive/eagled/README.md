# eagled

5Hz lateral-situation perception layer, plus its first consumer: lateral
avoidance. The perception core fuses radar tracks with a YOLO VRU detector
into the per-frame target picture; the avoidance planner gates that picture
(BSM / road-edge / speed / lane-change) and produces a small curvature bias
added on top of the model curvature, published on the existing
`lateralManeuverPlan` hook. controlsd only consumes it when
`AvoidanceEnabled` is on and the message envelope `valid` flag is set;
otherwise it falls back to the raw model curvature, which is also what an
invalid frame carries.

Perception runs whenever the device is onroad in a car (`eagle_run` in
process_config); `AvoidanceEnabled` gates only the avoidance actuation, in
process — turning avoidance off never turns the eagle's eyes off.

## Streams

| stream | role |
|---|---|
| `eagleState` | formal perception picture (in-gate fused targets, side inputs, geometry, sensor health) — for consumers; desire_helper (modeld, lane-change gating) is the planned second one |
| `eagleDebug` | raw detection/association telemetry + planner decision snapshot — lanlink UI and calibration only, never a control input |
| `lateralManeuverPlan` | avoidance actuation (`model + 2·bias/L²`), upstream hook consumed by controlsd's `fuse_curvature` |

| piece | file |
|---|---|
| 5Hz process, three-stream publish + `valid` flag | `eagled.py` |
| perception core: fusion chain, lazy camera/YOLO lifecycle, radar-only degrade; fusion primitives (`Target`/`_in_gate`/`fuse_targets`/`radar_point_key`) | `perception.py` |
| planner: gates, BSM, low-pass, hysteresis (decision layer) | `avoidance_planner.py` |
| camera feed: visionipc -> NV12 -> RGB -> bottom ROI 640x384 | `camera_stream.py` |
| box bottom-centre -> car-frame ground point + ROI inverse mapping | `projection.py` |
| radar<->vision nearest-neighbour association (shared with shadow) | `association.py` |
| tunables (offset caps, gates, weights, speed, camera mount) | `constants.py` |
| offline shadow harness (proxy vision, or detector-injected fused path) | `shadow.py` |
| online calibration collector (pairId residuals -> constant increments) | `calibrate.py` |
| YOLO detector shell + build recipe | `yolo_detector.py`, `models/README.md` |

## Fusion chain (per 5Hz tick)

`camera_stream.frame()` -> `YoloDetector.infer()` -> `project_detections()`
(ROI px inverse-mapped to full-frame px, then ground-plane projection; `dRel`
aligned to the radar's front-bumper origin via `CAMERA_TO_FRONT`) ->
`associate()` (matched detections are absorbed by their radar point, unmatched
ones — typically VRUs the radar missed — stay independent) ->
`fuse_targets(radar.points, fused)` -> planner. Without camerad or without the
YOLO pkl the daemon logs the reason once and degrades to radar-only.

## P0 shadow (record only, never publish)

`AvoidanceEnabled` defaults to **off**. With it off, `shadow.py` replays the
planner over a route and records what it *would* have done without sending
anything — the "bias vs projection alignment" check from the design doc §4.
Run it over 30 min of representative driving before enabling anything:

```bash
python -m openpilot.selfdrive.eagled.shadow <route> --out /tmp/shadow
# -> /tmp/shadow/shadow.csv        (per-frame bias / association / jerk / latency)
# -> /tmp/shadow/shadow_summary.json
```

The summary is graded against the P0 thresholds. A single global residual
threshold is physically unreachable beyond ~10 m: the ground-plane projection's
dRel sensitivity to pitch would demand 0.203° pitch accuracy at 10 m, 0.051° at
20 m and 0.013° at 40 m for a 0.30 m p95, while vehicle pitch swings ~1° under
braking and openpilot's own calibration lands in the 0.1–0.2° range. Grading is
therefore **banded by distance**; beyond 25 m only the bearing residual is
graded — bearing is what a monocular camera actually measures well (independent
of pitch and the ground-plane assumption):

| 距离档 | 距离残差 p95 | 关联率 |
|---|---|---|
| ≤ 10m | < 0.40m | > 0.85 |
| 10–25m | < 1.2m | > 0.75 |
| 25–40m | 不作距离判据(方位角残差 < 0.6°) | > 0.60 |

**关联率定义(写死):** `被匹配的视觉检测数 / 落在雷达视野内且 D_GATE 以内的视觉检测数`。
分母排除雷达物理上看不到的目标(雷达 FOV 外或 D_GATE 以外),否则指标衡量的
是雷达覆盖范围而不是视觉质量;互斥匹配保证比率 ≤ 1。注意 `shadow.py` 的
replay 汇总目前仍以全部雷达目标为分母(见其源码 NOTE),上表定义是设备端 P0
采集的目标口径。

其余判据(不分档):max lateral jerk < 5.0 m/s³(`drive_helpers.MAX_LATERAL_JERK`);
p95 planner latency < 200 ms(5Hz budget,planner only;measure the full process
on-device)。`pass` 还要求 **execution_closure** 达标(见三层验证 layer ②:
每个激活段的 closure ratio 与目标 yRel 实际漂移量)。

`pass` is false when a graded metric is out of range. `insufficient_data` is true
when the route had no in-gate radar targets (association cannot be judged). An
empty distance band reports `pass: None` — 没有数据的档位不视为通过,否则门限
会在静默中失去把关作用。

### 阴性场景集(不该触发)

P0 不仅要证明"该避让时避让",还要证明"不该触发时不触发"。以下场景在 shadow
记录里必须逐一 review(预期 ~0 触发;`shadow.csv` 里 valid 且非零 bias 且无
关联目标的帧即疑似误触发):

1. 对向车道来车(对向目标 yRel 大、接近快,容易扫进门限)
2. 匝道汇入 / 分流(横向相对运动大,方位角变化快)
3. 雨天路面反光(雷达杂波与视觉误检叠加)
4. 隧道出入口(标定与曝光最不稳)
5. 过减速带 / 坑洼(瞬时 pitch 剧变,地平面投影距离跳变)
6. **上下坡** —— 地平面假设在坡道上直接失效,必须单列

### Vision metrics need calibration first

未标定时视觉路径整体 gate off:`eagled._detect` 在 `extrinsicsCalibration`
未达 `calibrated` 时直接 `_degrade("calibration")` 并返回空,投影根本不运行。
所以 P0 的视觉相关指标(关联率、距离/方位角残差、分档表)必须在
`extrinsicsCalibration` 达到 `calibrated` 之后才开始采集;此前采集的帧只有
雷达侧数据,不能计入视觉判据。

Recorded per frame: model curvature, planner curvature, `valid`, `y_des` (m),
radar/vision/associated counts, lateral residual, latency, jerk. Use
`shadow.csv` to eyeball false triggers: a false trigger is a `valid` frame with
a non-zero bias and zero radar↔vision association. On the proxy path that means
a radar ghost; on the fused path it also flags legitimate vision-only biases (a
VRU the radar missed), so review the frames instead of reading the count as
failures.

### Vision side: proxy or fused path

The shadow harness has two vision sides:

- **Default — `modelV2.leadsV3` proxy.** Route logs carry no camera frames and
  no YOLO boxes, so a replay grades radar↔model-lead agreement. This is what
  `python -m openpilot.selfdrive.eagled.shadow <route>` runs.
- **Injected detector — real fused path.** `ShadowEvaluator(detector=...)`
  runs the daemon chain per frame: `detector.infer(roi)` →
  `project_detections()` (ground-plane projection, `CAMERA_TO_FRONT`-aligned)
  → `association.associate()` (2 m/1 m daemon gates) → `fuse_targets()`;
  `n_vision` then counts projected detections. Frames must carry synthetic
  camera data (`roi_frame`/`roi_meta`/`intrinsics`) — tests only; route logs
  cannot produce them.

The real P0 run is the **eagled daemon on the device** (real camera, real
YOLO pkl) with metrics collected over lanlink/CSV; the replay tool grades
offline planner metrics (bias, jerk, latency) on route logs.

## Device acceptance checklist (P0)

Everything below runs on the comma 4 (mici) with `AvoidanceEnabled` **off** — nothing
publishes until P1.

1. **Compile the YOLO pkl on-device, on 12V.** Mici powers CPU 4-7 down unless
   12V is on, and the compile needs CPU 4:
   ```bash
   openpilot/selfdrive/eagled/models/compile_yolo.sh   # -> models/yolo_tinygrad.pkl
   ```
2. **YOLO inference + ROI latency < 120 ms.** Measure per `models/README.md` §4
   (`DEV=QCOM`, 20 runs, report min/median). Over budget: drop ROI
   resolution/fps before anything else.
3. **Hand-calibrate only `CAMERA_TO_FRONT`.** Pitch/yaw/roll come from
   openpilot's live `extrinsicsCalibration` and the projection no longer reads
   `CAMERA_PITCH`/`CAMERA_YAW` — never patch them into constants. The one
   quantity live calibration does not provide is the longitudinal camera→bumper
   mount offset: initial values live in `constants.py` (`CAMERA_HEIGHT` 1.2 m,
   `CAMERA_TO_FRONT` 1.5 m) and the full workflow is the
   [Calibration & physical-realism verification](#calibration--physical-realism-verification-标定与物理真实性验证)
   section below. Accept when `calibrate.py`'s **banded** verdict passes
   (distance p95 per band, bearing p95 < 0.6° past 25 m; exit 0).
4. **30 min real-route shadow.** Run the daemon for ≥30 min of representative
   driving and grade the lanlink/CSV telemetry against the **banded** P0
   thresholds above (distance p95 / bearing / association rate per band), the
   `execution_closure` metrics, and false triggers reviewed in the per-frame
   records (expect ~0, including the negative-scenario list). Max lateral
   jerk < 5.0 m/s³.
5. **P1 release conditions** — only after 1-4 pass: enable `AvoidanceEnabled`
   for straight, daytime driving with `AvoidanceMaxLateralOffset` capped at
   **0.25 m**; confirm the jerk limit is never exceeded (everything still
   passes `clip_curvature`) and that any takeover immediately drops the bias
   (`steeringPressed` gate + the 1 s freshness gate).

## Calibration & physical-realism verification (标定与物理真实性验证)

The radar is the metric ground truth in the car frame (factory calibrated). The
daemon's `eagleDebug` stream stamps every associated radar↔vision pair with
a shared `pairId`, which gives the same object's position from both sources.

**Division of labour (Task 7):** pitch/yaw/roll are openpilot's job — the
projection takes them from live `extrinsicsCalibration`
(`projection.CalibratedGeometry`) and no longer reads `CAMERA_PITCH`/
`CAMERA_YAW`, so manually fitting them here would produce numbers nothing
consumes and invite "correcting" a calibration openpilot maintains
continuously. The only quantity live calibration cannot provide is the
longitudinal camera→bumper mount offset, so that is all this tool fits.

### Online calibration (`calibrate.py`)

`calibrate.py` is a standalone collector process (it never publishes — it only
subscribes to `eagleDebug`). Run it **on the device** while driving:

```bash
python -m openpilot.selfdrive.eagled.calibrate [--duration 120] [--min-pairs 30] [--max-pairs 500]
```

**lanlink 一键版（推荐）**：避让监测图状态条右侧的"开始标定/停止标定"按钮
走同一套拟合（`POST /api/calibration/start|stop`，`GET /api/calibration/status`），
无固定时长，开/停由你控制；停止后页面直接显示 p95 残差、Δfront、警告和
**可复制的 constants.py 建议块**（Δpitch/Δyaw 不再拟合 —— 在线标定负责，
页面显示为 "—"）。配对 <30 时结果标记"仅供参考"。

Workflow:

1. Turn `AvoidanceEnabled` on so the eagled process runs at all — the
   process itself is gated on the param (`avoidance_run` in
   `process_config.py`: onroad + car + param). Calibration does **not** require
   avoidance manoeuvres: `eagleDebug`, including the pairId-matched
   targets, is published every frame the daemon runs, regardless of planner
   validity or bias — but the vision side only runs once `extrinsicsCalibration`
   reports `calibrated` (see [above](#vision-metrics-need-calibration-first)).
   Drive with real lead vehicles ahead at **varied distances** so all three
   bands get pairs; 2-10 minutes is plenty.
2. Run `calibrate` while driving (or over a recorded `eagleDebug` session).
   It collects paired `(d_radar, y_radar, d_vision, y_vision, vEgo)` samples.
   **Only `CAMERA_TO_FRONT` is fitted**: the forward residual
   `e_d = d_vis − d_radar` is regressed on basis `[1]` (constant only) →
   `CAMERA_TO_FRONT += Δfront`. Everything else is diagnostic, reported but
   never folded into a constant:
   - a **constant** lateral residual (`e_y` intercept) → lateral mount-offset
     warning: the camera/radar origins are sideways of each other; fix it
     physically;
   - a **distance-growing** forward residual → pitch error, a
     **distance-growing** lateral residual (`e_y` slope = `−Δyaw`) → yaw error:
     both are `extrinsicsCalibration`'s job — the tool warns and tells you to
     re-collect after it reports `calibrated`;
   - the **banded residual table** (≤10 m / 10–25 m / 25–40 m, see the P0
     criteria above) grades the post-correction residuals; empty bands report
     `pass: None` and are listed as a coverage warning.
3. Paste the printed `constants.py` block (one line: `CAMERA_TO_FRONT`),
   rebuild, and re-run. **Iteration semantics:** the vision coordinates already
   include the constant currently compiled in, so the fitted delta is an
   *increment* — 1-2 rounds converge.
4. Accept when the **banded verdict** passes (the tool exits 0; exit 1 means
   insufficient pairs or a populated band out of tolerance).

The report shows pair count, vEgo range, forward residual p95 before/after the
`CAMERA_TO_FRONT` increment, lateral residual p95 (report only), the banded
table, warnings, and the banded pass/fail verdict.

### Static tape-measure spot check (静态卷尺抽查)

Before trusting the online fit, sanity-check the projection against physically
measured positions:

1. Place a large cardboard box or corner reflector at a known distance ahead
   (tape-measure from the front bumper, e.g. 10 m / 20 m / 30 m) and a known
   lateral offset (tape from car centreline, e.g. ±1 m, keep |yRel| ≤ 2.5 m so
   it stays in-gate).
2. Park with the target visible, run the daemon, and read the target's
   `dRel`/`yRel` from `eagleDebug` (lanlink bird's-eye view or a log tap).
3. Compare against the tape values: a constant dRel error → `CAMERA_TO_FRONT`
   (the one constant this tool fits); dRel error growing with distance → pitch
   error and yRel error growing with distance → yaw error — both are
   `extrinsicsCalibration`'s job now, so a persistent growth means recalibration
   has not converged (or the mount physically moved), not a constants.py edit.
   This cross-checks the online fit with independent ground truth and catches
   gross mount errors the regression could absorb.

### Three-layer physical-realism verification (三层物理真实性验证)

| layer | what it proves | tool / metric | gate |
|---|---|---|---|
| ① Perception | the vision projection agrees with the radar truth | `calibrate.py` banded verdict (distance p95 per band; bearing p95 < 0.6° past 25 m) | perception |
| ② Execution | the plan actually moves the car as commanded | `shadow.py` `execution_closure` metrics (below) | execution |
| ③ Final | end-to-end behaviour is correct on video | record a run, review the lanlink avoidance view against the road | final acceptance |

Layer ② in detail — `shadow.py` extends its summary with an
`execution_closure` field (also printed per segment in the terminal). Per
activation segment it records:

- **target yRel drift** — nearest in-gate target's `yRel`, segment end minus
  start: the object the avoidance pushes away from should actually recede
  laterally;
- **yDes mean** — the executed (post-low-pass) offset command over the segment;
  the summary also carries `y_des_cmd_mean_m`, the raw pre-low-pass command;
- **road-edge clearance change** on the avoidance side: the manoeuvre must not
  eat into the `EDGE_CLEAR_MIN` margin (shadow records use `None` for "no edge
  visible" where the daemon's `eagleDebug` message sends `999.0` for the
  same condition — don't compare the two directly);
- **closure ratio** — displacement integrated from the executed curvature bias
  (`∫∫ v²·(curvature − model_curvature) dt²`, post-low-pass: what the plan
  actually bends) vs the displacement the **raw** pre-low-pass command implies
  (`∫∫ v²·2·yDes_cmd/L² dt²`). Both terms share the left-positive y convention;
  the ratio (executed / commanded) deliberately compares the executed plan
  against the raw command: in shadow replay the gap is the low-pass lag
  (→ 1 for steady segments); on device, computing the same metric from
  telemetry curvature closes the loop on the real vehicle response
  (actual displacement ≈ `max_offset` when avoidance activates).

```bash
python -m openpilot.selfdrive.eagled.shadow <route> --out /tmp/shadow
# summary JSON now carries "execution_closure"; main() also prints a
# per-segment block to the terminal
```

### Known limitations (已知限制)

- **Segment target drift can switch objects**: the per-segment yRel drift tracks
  whichever in-gate target is nearest each frame — if the nearest target changes
  mid-segment, the drift mixes two objects' motion and is not a single-object
  closure signal.
- **Lateral intercept aliases mount offset with the Δyaw·CAMERA_TO_FRONT cross
  term**: the yaw rotation acts about the camera origin (`d_r + CAMERA_TO_FRONT`)
  while the diagnostic regression basis uses bumper-frame `d_r`, so part of a
  pure yaw error shows up as intercept — a small intercept is not proof of a
  physical mount offset (and vice versa). Yaw is no longer fitted, so the
  cross-term only slightly pollutes the lateral-bias *diagnostic*; the
  distance-growing part is flagged as yaw contamination instead.
- **The `CAMERA_TO_FRONT` suggestion absorbs a pitch cross-term**: the constant
  fit cannot separate the `Δpitch·h` piece of a pitch error from a true forward
  shift, so with pitch contamination the suggestion carries a pitch-dependent
  component. The contamination warning fires in that case — re-collect after
  `extrinsicsCalibration` converges instead of pasting.
- **Curvature double integral ignores initial lateral velocity**: the measured
  displacement assumes zero lateral velocity at segment start, so it is relative
  to the segment-start state, not absolute.

## P0 concerns (deferred limitations)

- **ROI inverse mapping omits the half-pixel centre term** (`u_full = u·scale`
  instead of `(u+0.5)·scale − 0.5`): ~0.5 px systematic bias, equivalent to a
  small pitch offset — absorbed by openpilot's live pitch calibration.
- **Fisheye distortion unmodelled**: the wide-road camera config is a pinhole
  approximation of a fisheye module; projection error grows toward the frame
  edges (distant/small targets).
- **`_degrade("camera")` reason aliasing**: one bucket covers both "no camerad
  stream" and "no fresh frame this tick"; log-once keeps it harmless,
  diagnostics only.
- **`nearest_pairs` is not one-to-one**: two radar points can each match the
  same detection, inflating the shadow `n_associated` (the daemon does not
  consume that count). Greedy nearest-neighbour limitation, bounded by the
  gates.
- **No per-tick fusion-stat export yet**: the daemon computes association
  counts per tick but publishes only the plan; the lanlink/CSV tap for
  checklist item 4 is part of P0 device bring-up.
- **Shadow metric semantics differ from the daemon path**: the shadow
  association metric gates at 3.0 m / 1.5 m while the daemon absorbs
  detections at 2.0 m / 1.0 m, and `n_vision` counts all projected detections
  unfiltered whereas `n_radar` counts only in-gate radar targets. The
  association rate is therefore optimistic as a P0 gate signal, and its
  denominator (all radar targets) is not yet the P0 definition written above
  (vision detections within the radar FOV and `D_GATE`); when reviewing
  `shadow.csv` false triggers, read the counts against these definitions, not
  the daemon's tighter gates.

## P1 small open

After the device checklist passes: enable `AvoidanceEnabled` for straight,
daytime driving with `AvoidanceMaxLateralOffset` capped at 0.25 m (checklist
item 5). Everything still passes through `clip_curvature`, so the jerk/accel
limits hold by construction; the P1 run confirms it on the car.

```bash
pytest openpilot/selfdrive/eagled/ openpilot/selfdrive/controls/tests/ -q
```

## Replay

`process_replay` has an `eagled` config (inputs `modelV2`, `carState`,
`radarTracks`; output `lateralManeuverPlan` at 5Hz). It is in `EXCLUDED_PROCS`
and **no reference log exists for it**, so `--whitelist-procs eagled`
cannot produce a passing comparison today — there is nothing to diff against.
The whitelist flag becomes useful only after a reference log is generated
(ref-commit pipeline or a device run); until then the config is a structural
check of the pubs/subs wiring, not a runnable regression test.
