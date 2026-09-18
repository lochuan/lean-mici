# avoidanced

5Hz decision-level lateral avoidance: radar targets (optionally upgraded by a
YOLO VRU detector) produce a small curvature bias that is added on top of the
model curvature and published on the existing `lateralManeuverPlan` hook.
controlsd only consumes it when `AvoidanceEnabled` is on and the message
envelope `valid` flag is set; otherwise it falls back to the raw model
curvature, which is also what an invalid frame carries.

| piece | file |
|---|---|
| 5Hz process, every-frame publish + `valid` flag | `avoidanced.py` |
| planner: gates, BSM, low-pass, hysteresis | `avoidance_planner.py` |
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
python -m openpilot.selfdrive.avoidanced.shadow <route> --out /tmp/shadow
# -> /tmp/shadow/shadow.csv        (per-frame bias / association / jerk / latency)
# -> /tmp/shadow/shadow_summary.json
```

The summary is graded against the P0 thresholds:

| metric | threshold | source |
|---|---|---|
| radar↔vision association rate | > 0.80 | Task 6 brief |
| calibration max lateral residual | < 0.30 m | design §4 |
| max lateral jerk (`v² · dcurv/dt`) | < 5.0 m/s³ | `drive_helpers.MAX_LATERAL_JERK` |
| p95 planner latency | < 200 ms | 5Hz budget (planner only; measure the full process on-device) |

`pass` is false when a graded metric is out of range. `insufficient_data` is true
when the route had no in-gate radar targets (association cannot be judged).

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
  `python -m openpilot.selfdrive.avoidanced.shadow <route>` runs.
- **Injected detector — real fused path.** `ShadowEvaluator(detector=...)`
  runs the daemon chain per frame: `detector.infer(roi)` →
  `project_detections()` (ground-plane projection, `CAMERA_TO_FRONT`-aligned)
  → `association.associate()` (2 m/1 m daemon gates) → `fuse_targets()`;
  `n_vision` then counts projected detections. Frames must carry synthetic
  camera data (`roi_frame`/`roi_meta`/`intrinsics`) — tests only; route logs
  cannot produce them.

The real P0 run is the **avoidanced daemon on the device** (real camera, real
YOLO pkl) with metrics collected over lanlink/CSV; the replay tool grades
offline planner metrics (bias, jerk, latency) on route logs.

## Device acceptance checklist (P0)

Everything below runs on the comma 4 (mici) with `AvoidanceEnabled` **off** — nothing
publishes until P1.

1. **Compile the YOLO pkl on-device, on 12V.** Mici powers CPU 4-7 down unless
   12V is on, and the compile needs CPU 4:
   ```bash
   openpilot/selfdrive/avoidanced/models/compile_yolo.sh   # -> models/yolo_tinygrad.pkl
   ```
2. **YOLO inference + ROI latency < 120 ms.** Measure per `models/README.md` §4
   (`DEV=QCOM`, 20 runs, report min/median). Over budget: drop ROI
   resolution/fps before anything else.
3. **Calibrate pitch / yaw / CAMERA_TO_FRONT.** Initial mount values live in
   `constants.py` (`CAMERA_HEIGHT` 1.2 m, `CAMERA_PITCH` 0, `CAMERA_YAW` 0,
   `CAMERA_TO_FRONT` 1.5 m) — the full workflow is the
   [Calibration & physical-realism verification](#calibration--physical-realism-verification-标定与物理真实性验证)
   section below. Quick order of attack: `CAMERA_TO_FRONT` for a constant dRel
   shift, `CAMERA_PITCH` (down-positive) for dRel error that grows with
   distance, `CAMERA_YAW` (left-positive) for a lateral error that grows with
   distance. Accept when the post-correction residual p95 stays < 0.30 m
   (spec §4: 0.3 m is the unacceptable threshold).
4. **30 min real-route shadow.** Run the daemon for ≥30 min of representative
   driving and grade the lanlink/CSV telemetry against the P0 thresholds:
   association rate > 0.80, calibration max lateral residual < 0.30 m, false
   triggers reviewed in the per-frame records (expect ~0), max lateral jerk
   < 5.0 m/s³.
5. **P1 release conditions** — only after 1-4 pass: enable `AvoidanceEnabled`
   for straight, daytime driving with `AvoidanceMaxLateralOffset` capped at
   **0.25 m**; confirm the jerk limit is never exceeded (everything still
   passes `clip_curvature`) and that any takeover immediately drops the bias
   (`steeringPressed` gate + the 1 s freshness gate).

## Calibration & physical-realism verification (标定与物理真实性验证)

The radar is the metric ground truth in the car frame (factory calibrated). The
daemon's `avoidanceDebug` stream stamps every associated radar↔vision pair with
a shared `pairId`, which gives the same object's position from both sources.
The residual `vision − radar` decomposes into the three projection-constant
errors, and that is what the calibration tooling below fits and verifies.

### Online calibration (`calibrate.py`)

`calibrate.py` is a standalone collector process (it never publishes — it only
subscribes to `avoidanceDebug`). Run it **on the device** while driving:

```bash
python -m openpilot.selfdrive.avoidanced.calibrate [--duration 120] [--min-pairs 30] [--max-pairs 500]
```

Workflow:

1. Turn `AvoidanceEnabled` on so the avoidanced process runs at all — the
   process itself is gated on the param (`avoidance_run` in
   `process_config.py`: onroad + car + param). Calibration does **not** require
   avoidance manoeuvres: `avoidanceDebug`, including the pairId-matched
   targets, is published every frame the daemon runs, regardless of planner
   validity or bias. Drive with real lead vehicles ahead at **varied
   distances** (the fit separates a constant `CAMERA_TO_FRONT` term from the
   distance-growing pitch term only if pairs span a wide distance range);
   2-10 minutes is plenty.
2. Run `calibrate` while driving (or over a recorded `avoidanceDebug` session).
   It collects paired `(d_radar, y_radar, d_vision, y_vision, vEgo)` samples and
   least-squares fits:
   - forward residual `e_d = d_vis − d_radar` on basis `[1, d_r²/h]` →
     `CAMERA_TO_FRONT += Δfront`, `CAMERA_PITCH += Δpitch`;
   - lateral residual `e_y = y_vis − y_radar` on basis `[1, d_r]` → the slope
     is `−Δyaw` (the projection *undoes* the camera yaw, so a camera truly
     yawed left by Δyaw maps forward distance to `e_y = −Δyaw·d`); the tool
     suggests `CAMERA_YAW += −slope`. A constant intercept is reported as a
     **lateral mount-offset warning** — it means the camera/radar origins are
     sideways of each other; fix it physically, never patch it into a constant.
3. Paste the printed `constants.py` block, rebuild, and re-run. **Iteration
   semantics:** the vision coordinates already include the constants currently
   compiled in, so the fitted deltas are *increments* — 1-2 rounds converge.
4. Accept when the post-correction residual p95 < 0.30 m (the tool exits 0;
   exit 1 means insufficient pairs or out-of-tolerance fit).

The report shows pair count, vEgo range, residual p95 before/after correction,
per-constant suggestions, and pass/fail vs 0.3 m.

### Static tape-measure spot check (静态卷尺抽查)

Before trusting the online fit, sanity-check the projection against physically
measured positions:

1. Place a large cardboard box or corner reflector at a known distance ahead
   (tape-measure from the front bumper, e.g. 10 m / 20 m / 30 m) and a known
   lateral offset (tape from car centreline, e.g. ±1 m, keep |yRel| ≤ 2.5 m so
   it stays in-gate).
2. Park with the target visible, run the daemon, and read the target's
   `dRel`/`yRel` from `avoidanceDebug` (lanlink bird's-eye view or a log tap).
3. Compare against the tape values: a constant dRel error → `CAMERA_TO_FRONT`;
   dRel error growing with distance → `CAMERA_PITCH`; yRel error growing with
   distance → `CAMERA_YAW`. This cross-checks the online fit with independent
   ground truth and catches gross mount errors the regression could absorb.

### Three-layer physical-realism verification (三层物理真实性验证)

| layer | what it proves | tool / metric | gate |
|---|---|---|---|
| ① Perception | the vision projection agrees with the radar truth | `calibrate.py` post-correction residual p95 < 0.3 m | perception |
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
  visible" where the daemon's `avoidanceDebug` message sends `999.0` for the
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
python -m openpilot.selfdrive.avoidanced.shadow <route> --out /tmp/shadow
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
  while the regression basis uses bumper-frame `d_r`, so part of a pure yaw
  error shows up as intercept — a small intercept is not proof of a physical
  mount offset (and vice versa).
- **Forward constant term re-absorbs a pitch cross-term every round**: the pitch
  basis `[1, d²/h]` ignores the constant `Δpitch·h` piece of the true pitch
  error, so each round's `CAMERA_TO_FRONT` suggestion carries a small
  pitch-dependent component; convergence still holds (verified by round-trip
  test) but the split between the two constants is approximate.
- **Curvature double integral ignores initial lateral velocity**: the measured
  displacement assumes zero lateral velocity at segment start, so it is relative
  to the segment-start state, not absolute.

## P0 concerns (deferred limitations)

- **ROI inverse mapping omits the half-pixel centre term** (`u_full = u·scale`
  instead of `(u+0.5)·scale − 0.5`): ~0.5 px systematic bias, equivalent to a
  small pitch offset — absorbed by the P0 pitch calibration.
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
  association rate is therefore optimistic as a P0 gate signal; when reviewing
  `shadow.csv` false triggers, read the counts against these definitions, not
  the daemon's tighter gates.

## P1 small open

After the device checklist passes: enable `AvoidanceEnabled` for straight,
daytime driving with `AvoidanceMaxLateralOffset` capped at 0.25 m (checklist
item 5). Everything still passes through `clip_curvature`, so the jerk/accel
limits hold by construction; the P1 run confirms it on the car.

```bash
pytest openpilot/selfdrive/avoidanced/ openpilot/selfdrive/controls/tests/ -q
```

## Replay

`process_replay` has an `avoidanced` config (inputs `modelV2`, `carState`,
`radarTracks`; output `lateralManeuverPlan` at 5Hz). It is in `EXCLUDED_PROCS`
and **no reference log exists for it**, so `--whitelist-procs avoidanced`
cannot produce a passing comparison today — there is nothing to diff against.
The whitelist flag becomes useful only after a reference log is generated
(ref-commit pipeline or a device run); until then the config is a structural
check of the pubs/subs wiring, not a runnable regression test.
