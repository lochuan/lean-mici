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
| offline shadow harness | `shadow.py` |
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
a non-zero bias whose target no vision object corroborated (a radar ghost).

### Known gap: the shadow vision side is a stand-in

The daemon runs the real camera -> YOLO -> projection -> association chain, but
the shadow replay still uses `modelV2.leadsV3` at t=0 as the vision-side object
because route logs carry no YOLO boxes — so the shadow association rate and
calibration residual measure radar↔model-lead agreement, not radar↔YOLO-box
agreement. `shadow.associate()` shares the matcher core with the daemon path
(`association.py`) and accepts projected YOLO boxes as-is; the detector's own
latency is measured separately per `models/README.md` §4.

## P1 small open

After P0 is clean: enable `AvoidanceEnabled` for straight, daytime driving with
`AvoidanceMaxLateralOffset` capped at 0.25 m and confirm the fused curvature
never exceeds the jerk/accel limits (everything still passes through
`clip_curvature`) and that any takeover immediately drops the bias.

```bash
pytest openpilot/selfdrive/avoidanced/ openpilot/selfdrive/controls/tests/ -q
```

## Replay

`process_replay` has an `avoidanced` config (inputs `modelV2`, `carState`,
`radarTracks`; output `lateralManeuverPlan` at 5Hz). It is in
`EXCLUDED_PROCS` because there are no reference logs for it yet; replay it
explicitly with `--whitelist-procs avoidanced`.
