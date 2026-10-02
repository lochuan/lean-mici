# eagled

5Hz lateral-situation perception layer, plus its first consumer: in-lane
avoidance. The perception core fuses radar tracks with a YOLO detector into the
per-frame target picture; `lane_offset.LaneOffsetPlanner` turns adjacent-lane
targets into side pressures and a lane offset target (m, left positive),
published as `lateralManeuverPlan.desiredLaneOffset` every frame. controlsd
closes the loop on the current frame's lane lines (clamped to the lane-edge
cap) when `AvoidanceEnabled` is on and the envelope `valid` flag is set;
otherwise it falls back to the raw model curvature, which is also what an
invalid frame carries.

Perception runs whenever the device is onroad in a car (`eagle_run` in
process_config); `AvoidanceEnabled` gates only the avoidance actuation and the
vision chain (camera + YOLO) — with it off the core degrades to radar-only and
all streams keep publishing.

## Streams

| stream | role |
|---|---|
| `eagleState` | formal perception picture (adjacent-lane fused targets, side inputs, lane-change clearance, geometry, sensor health) — for consumers; desire_helper (modeld) reads the clearance |
| `eagleDebug` | raw detection/association telemetry + planner decision snapshot — lanlink UI and calibration only, never a control input; logged to rlog |
| `lateralManeuverPlan` | `desiredLaneOffset` + `gapInsufficient`, consumed by controlsd (offset loop) and selfdrived (gap-insufficient alert) |

| piece | file |
|---|---|
| 5Hz process, three-stream publish + `valid` flag | `eagled.py` |
| perception core: fusion chain, lazy camera/YOLO lifecycle, radar-only degrade (`Target`/`fuse_objects`) | `perception.py` |
| lane geometry, side pressure, offset planner, lane-change clearance, controlsd closed-loop correction | `lane_offset.py` |
| camera feed: visionipc -> NV12 -> RGB -> bottom ROI 640x384 | `camera_stream.py` |
| box bottom-centre -> car-frame ground point + ROI inverse mapping | `projection.py` |
| radar<->vision nearest-neighbour association | `association.py` |
| tunables (Params-overridable table in `_PARAM_OVERRIDABLE`) | `constants.py` |
| online calibration collector (pairId residuals -> constant increments) | `calibrate.py` |
| YOLO detector shell + build recipe | `yolo_detector.py`, `models/README.md` |

## Fusion chain (per 5Hz tick)

`camera_stream.frame()` -> `YoloDetector.infer()` -> `project_detections()`
(ROI px inverse-mapped to full-frame px, then ground-plane projection; `dRel`
aligned to the radar's front-bumper origin via `CAMERA_TO_FRONT`) ->
`associate()` (matched detections are absorbed by their radar point, unmatched
ones — typically VRUs the radar missed — stay independent) ->
`fuse_objects(radar.points, fused)` -> planner. Static radar points (ground
speed below `STATIC_SPEED_THRESH`) are dropped unless a vision detection
confirms them — guardrails and bridge piers must not move the car. Without
camerad or without the YOLO pkl the daemon logs the reason once and degrades
to radar-only.

## In-lane avoidance

All geometry is the model's lane lines in the vehicle frame (`lane_geometry`,
y left positive). Both ego lane lines must pass the confidence gate
(`LANE_PROB_MIN` / `LANE_STD_MAX`), otherwise the planner is inactive
(`inactiveReason = lane_untrusted`).

- **Line distance** — lateral distance from the near body edge of a target
  outside the ego lane (center +/- class half-width) to the ego lane line,
  interpolated at the target's `dRel`; negative = intruding.
- **Pressure** — `clamp((trigger - line_distance) / trigger, 0, 1)`; trigger is
  `TRIGGER_LINE_DISTANCE_VRU` / `TRIGGER_LINE_DISTANCE_VEHICLE` by class.
  Only targets within `TRIGGER_RANGE_MAX` and a time-to-arrival
  (`dRel / approach speed`) within `TIME_WINDOW_S` count. Vision-only targets
  have unknown speed: approach speed assumes `VISION_TARGET_SPEED`.
  Per side, the maximum over targets.
- **Parallel hold** — a target that leaves view inside the window keeps its
  last pressure until the dead-reckoned ego has passed it (+ `HOLD_PASS_MARGIN`)
  or `HOLD_MAX_S` expires.
- **Offset target** = lane-edge cap x (right pressure - left pressure); cap =
  lane half width - `EGO_HALF_WIDTH` - `LANE_EDGE_MARGIN` (0 when negative).
  Rate-limited by `OFFSET_RATE`.
- **Gap insufficient** — an intruding target whose lateral gap to the ego body
  at the offset target is below `LANE_EDGE_MARGIN`; raises the
  `gapInsufficient` warning in selfdrived.
- **Active gates** — `AvoidanceEnabled`, lateral active, hands off the wheel,
  not lane-changing, `V_EGO_MIN_KPH` <= speed <= `V_EGO_MAX`.

controlsd re-derives the cap on the current frame's lane lines, clamps the
target, and adds `2 * error / L_LOOKAHEAD^2` to the model curvature where
`error = target - (model path offset from lane center at L_LOOKAHEAD)`.
Untrusted current lane lines, stale/invalid plan or `AvoidanceEnabled` off ->
raw model curvature.

**Lane-change clearance** (`change_clear`, published as
`changeClearLeft/RightState` on both streams): only forward targets whose
center lies in the target lane count (ego lane line to the next lane line;
width-estimated when the outer line is unconfident). A target blocks when it is
inside `LANE_CHANGE_NEAR_D`, has unknown speed, or its position
`LANE_CHANGE_LEAD_TIME_S` ahead is not beyond the ego's `LANE_CHANGE_EGO_TIME_S`
position (the 4s/3s asymmetry gives the other car one extra second of margin).
Untrusted ego lane lines -> `unknown`, and desire_helper falls back to BSM +
road-edge checks.

## Calibration & physical-realism verification (标定与物理真实性验证)

The radar is the metric ground truth in the car frame (factory calibrated). The
daemon's `eagleDebug` stream stamps every associated radar↔vision pair with
a shared `pairId`, which gives the same object's position from both sources.

**Division of labour:** pitch/yaw/roll are openpilot's job — the projection
takes them from live `extrinsicsCalibration` (`projection.CalibratedGeometry`)
and does not read `CAMERA_PITCH`/`CAMERA_YAW`, so manually fitting them here
would produce numbers nothing consumes. The only quantity live calibration
cannot provide is the longitudinal camera→bumper mount offset, so that is all
this tool fits. The vision path is gated off until `extrinsicsCalibration`
reports `calibrated` (`PerceptionCore.detect` degrades with reason
`calibration`), so vision metrics can only be collected after that.

A single global residual threshold is physically unreachable beyond ~10 m: the
ground-plane projection's dRel sensitivity to pitch would demand 0.203° pitch
accuracy at 10 m, 0.051° at 20 m and 0.013° at 40 m for a 0.30 m p95, while
vehicle pitch swings ~1° under braking. Grading is therefore **banded by
distance**; beyond 25 m only the bearing residual is graded — bearing is what a
monocular camera actually measures well:

| 距离档 | 距离残差 p95 | 关联率 |
|---|---|---|
| ≤ 10m | < 0.40m | > 0.85 |
| 10–25m | < 1.2m | > 0.75 |
| 25–40m | 不作距离判据(方位角残差 < 0.6°) | > 0.60 |

An empty band reports `pass: None` — a band with no data is not a pass.

### Online calibration (`calibrate.py`)

`calibrate.py` is a standalone collector process (it never publishes — it only
subscribes to `eagleDebug`). Run it **on the device** while driving:

```bash
python -m openpilot.selfdrive.eagled.calibrate [--duration 120] [--min-pairs 30] [--max-pairs 500]
```

**lanlink 一键版（推荐）**：避让监测图状态条右侧的"开始标定/停止标定"按钮
走同一套拟合（`POST /api/calibration/start|stop`，`GET /api/calibration/status`），
无固定时长，开/停由你控制；停止后页面直接显示残差散布（p95）、Δfront、警告和
建议值（当前 → 建议），点**「保存并生效」**（`POST /api/calibration/apply`）即写
Params `CameraToFront`，下一帧生效，无需重编（票 #7）。防呆：配对 <30 或建议值
越出 0.5–2.5 m 时拒绝保存（409）并显示原因。

Workflow:

1. Turn `AvoidanceEnabled` on so the vision chain runs (the eagled process
   itself runs whenever onroad in a car). Calibration does **not** require
   avoidance manoeuvres: `eagleDebug`, including the pairId-matched
   targets, is published every frame regardless of planner validity — but the
   vision side only runs once `extrinsicsCalibration` reports `calibrated`.
   Drive with real lead vehicles ahead at **varied distances** so all three
   bands get pairs; 2-10 minutes is plenty.
2. Run `calibrate` while driving (or over a recorded `eagleDebug` session).
   It collects paired `(d_radar, y_radar, d_vision, y_vision, vEgo)` samples.
   **Only `CAMERA_TO_FRONT` is fitted**: the forward residual
   `e_d = d_vis − d_radar` is regressed on basis `[1]` (constant only) →
   Params `CameraToFront += Δfront`. Everything else is diagnostic, reported but
   never folded into a constant:
   - a **constant** lateral residual (`e_y` intercept) → lateral mount-offset
     warning: the camera/radar origins are sideways of each other; fix it
     physically;
   - a **distance-growing** forward residual → pitch error, a
     **distance-growing** lateral residual (`e_y` slope = `−Δyaw`) → yaw error:
     both are `extrinsicsCalibration`'s job — the tool warns and tells you to
     re-collect after it reports `calibrated`;
   - the **banded residual table** (≤10 m / 10–25 m / 25–40 m, see the band table above) grades the post-correction residuals; empty bands report
     `pass: None` and are listed as a coverage warning.
3. The tool writes the new value to Params `CameraToFront` itself (consumers
   pick it up next frame, no rebuild) — but only when the save guard passes:
   at least 30 pairs and a result within the physical 0.5–2.5 m range;
   otherwise it prints `NOT SAVED` with the reason. Re-run to verify.
   **Iteration semantics:** the vision coordinates already include the value
   currently in effect, so the fitted delta is an *increment* on it — 1-2
   rounds converge.
4. Accept when the **banded verdict** passes (the tool exits 0; exit 1 means
   insufficient pairs or a populated band out of tolerance).

The report shows pair count, vEgo range, forward residual p95 before/after the
`CameraToFront` increment, lateral residual p95 (report only), the banded
table, warnings, the banded pass/fail verdict, and the current → proposed
`CameraToFront` value.


### Static tape-measure spot check (静态卷尺抽查)

Before trusting the online fit, sanity-check the projection against physically
measured positions:

1. Place a large cardboard box or corner reflector at a known distance ahead
   (tape-measure from the front bumper, e.g. 10 m / 20 m / 30 m) and a known
   lateral offset (tape from car centreline, e.g. ±1 m, keep |yRel| ≤ 2.5 m so
   it stays in the radar/camera overlap).
2. Park with the target visible, run the daemon, and read the target's
   `dRel`/`yRel` from `eagleDebug` (lanlink bird's-eye view or a log tap).
3. Compare against the tape values: a constant dRel error → `CAMERA_TO_FRONT`
   (the one constant this tool fits); dRel error growing with distance → pitch
   error and yRel error growing with distance → yaw error — both are
   `extrinsicsCalibration`'s job now, so a persistent growth means recalibration
   has not converged (or the mount physically moved), not a constants.py edit.
   This cross-checks the online fit with independent ground truth and catches
   gross mount errors the regression could absorb.


### Known limitations (已知限制)

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


## Deferred limitations

- **ROI inverse mapping omits the half-pixel centre term** (`u_full = u·scale`
  instead of `(u+0.5)·scale − 0.5`): ~0.5 px systematic bias, equivalent to a
  small pitch offset — absorbed by openpilot's live pitch calibration.
- **Fisheye distortion unmodelled**: the wide-road camera config is a pinhole
  approximation of a fisheye module; projection error grows toward the frame
  edges (distant/small targets).
- **`_degrade("camera")` reason aliasing**: one bucket covers both "no camerad
  stream" and "no fresh frame this tick"; log-once keeps it harmless,
  diagnostics only.

## Replay

`process_replay` has an `eagled` config (inputs `modelV2`, `carState`,
`radarTracks`; output `lateralManeuverPlan` at 5Hz). It is in `EXCLUDED_PROCS`
and **no reference log exists for it**, so `--whitelist-procs eagled`
cannot produce a passing comparison today — there is nothing to diff against.
The whitelist flag becomes useful only after a reference log is generated
(ref-commit pipeline or a device run); until then the config is a structural
check of the pubs/subs wiring, not a runnable regression test.
