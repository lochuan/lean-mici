#!/usr/bin/env python3
"""04-E 车测统计（C4 侧）：超时率@48、连续超时、L̂ 分布、modeldLagging。

用法（设备上）:
  PYTHONPATH=/data/openpilot:/data/openpilot/openpilot \
    /usr/local/venv/bin/python openpilot/selfdrive/bigmodeld/stats_e2e.py \
    --duration 1800 --interval 60 | tee /data/stats_e2e.txt
（--duration 0 = 一直跑到 Ctrl-C；Ctrl-C 也打终表）

口径（04 号 + ADR-0001）：
  帧流 = modeld 的 modelV2 + modelDataV2SP（同一主循环逐帧连发；两流 logMonoTime
  差 > 50 ms 视为错帧，计 pair_miss 丢弃）。逐帧分类（classify）：
    warmup    头 4 帧——订阅起点 / 流断档 > 250 ms 之后（modeld 头 4 帧本来不等 REPLY）
    reconnect BigmodelLinkState ∈ {connecting, lost, ""} 期间——验收剔除「重连期间」
    ontime    modelV2.big=True：截止时刻内拿到大模型输出
    zeropair  big=False 且 bigLatencyMs>0 = 解码跳过帧（全零 outputs，14 号口径）——剔除
    timeout   big=False 且 bigLatencyMs==0 = 截止时刻没等到 REPLY（含晚到/链路没回音）
  超时率@48 = timeout / (ontime + timeout)；截止 = timestamp_eof + L̂ + 48 ms
  （BIG_REPLY_GRACE_MS，modeld.py:48——本脚本口径与择优逻辑同源，不重算截止）。
  连续超时 = 合格帧序列里 timeout 连跑 ≥3 的次数（被剔除帧打断连跑，保守计）。
  modeldLagging 判据 = modelV2.frameDropPerc > 1（selfdrived.py:448 同判据）。
  注意：ColorOS 冻结段没有可观测标志，冻结造成的断档按 warmup 剔前 4 帧，其余计数。
"""
import argparse
import sys
import time

from openpilot.cereal import messaging
from openpilot.common.params import Params

WARMUP_FRAMES = 4      # modeld.py BIG_WARMUP_FRAMES
GAP_MS = 250.0         # 帧流断档阈值（5 个帧周期）：之后按重启重新剔 warmup
PAIR_TOL_MS = 50.0     # modelV2 ↔ modelDataV2SP 同拍容差
RECONNECT_STATES = {"", "connecting", "lost"}
ELAPSED_MS = 1e6       # logMonoTime（ns）→ ms


def classify(big, reply_ms, warmup, link_state):
  """逐帧分类（顺序即优先级）。selftest 覆盖每个分支。"""
  if warmup:
    return "warmup"
  if link_state in RECONNECT_STATES:
    return "reconnect"
  if big:
    return "ontime"
  if reply_ms > 0:
    return "zeropair"
  return "timeout"


def count_streaks(timeout_flags):
  """连续 ≥3 帧超时的次数：每次连跑只记一次。"""
  n = streak = 0
  for t in timeout_flags:
    streak = streak + 1 if t else 0
    if streak == 3:
      n += 1
  return n


def pct(sorted_vals, p):
  if not sorted_vals:
    return 0.0
  return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def summarize(frames, pair_miss, elapsed_s, counts):
  """frames = [(cls, reply_ms, cam_ms, drop_perc), ...]；返回终表字符串。"""
  cls_n = {c: sum(1 for f in frames if f[0] == c) for c in
           ("ontime", "timeout", "zeropair", "warmup", "reconnect")}
  eligible = cls_n["ontime"] + cls_n["timeout"]
  rate = 100.0 * cls_n["timeout"] / eligible if eligible else 0.0
  hours = elapsed_s / 3600.0
  streaks = count_streaks(f[0] == "timeout" for f in frames)
  cam = sorted(f[2] for f in frames if f[2] > 0)
  reply = sorted(f[1] for f in frames if f[1] > 0)
  drops = sum(1 for f in frames if f[3] > 1)
  lines = [
    f"=== 04-E @48 统计（{elapsed_s:.0f} s，{len(frames)} 帧，pair_miss {pair_miss}）===",
    f"合格 {eligible} 帧（ontime {cls_n['ontime']} + timeout {cls_n['timeout']}）"
    f"｜剔除 warmup {cls_n['warmup']} / zeropair {cls_n['zeropair']} / 重连 {cls_n['reconnect']}",
    f"超时率@48: {rate:.2f}%（验收线 ≤5%）",
    f"连续 ≥3 超时: {streaks} 次（{streaks / hours if hours else 0:.1f} 次/时，验收线 ≤10）",
    f"cameraToModelMs（L_n）: P50 {pct(cam, 0.5):.2f} P99 {pct(cam, 0.99):.2f} max {cam[-1] if cam else 0:.2f}",
    f"bigLatencyMs（REPLY 往返，>0）: P50 {pct(reply, 0.5):.2f} P99 {pct(reply, 0.99):.2f}",
    f"frameDropPerc>1（modeldLagging 判据）: {drops} 帧",
    "link 状态帧数: " + " ".join(f"{k}={counts.get(k, 0)}" for k in
                              ("connected", "blip", "restart", "connecting", "lost")),
  ]
  return "\n".join(lines)


def run(duration_s, interval_s):
  sm = messaging.SubMaster(["modelV2", "modelDataV2SP"])
  params = Params()
  if not bool(params.get("BigmodelToggle", return_default=True)):
    print("警告：BigmodelToggle=off，modeld 不等 REPLY，全部帧将计 timeout", file=sys.stderr)

  frames, counts = [], {}
  pair_miss = 0
  warmup_left = WARMUP_FRAMES
  last_mono_ms = None
  t0 = time.monotonic()
  last_report = t0

  def tick():
    nonlocal warmup_left, last_mono_ms, pair_miss
    sm.update(100)
    if not sm.updated["modelDataV2SP"]:
      return
    v, m = sm["modelV2"], sm["modelDataV2SP"]
    mono_ms = m.logMonoTime / ELAPSED_MS
    if abs(v.logMonoTime - m.logMonoTime) / ELAPSED_MS > PAIR_TOL_MS:
      pair_miss += 1
      return
    if last_mono_ms is not None and mono_ms - last_mono_ms > GAP_MS:
      warmup_left = WARMUP_FRAMES
    last_mono_ms = mono_ms

    warmup = warmup_left > 0
    if warmup_left > 0:
      warmup_left -= 1
    link = params.get("BigmodelLinkState") or ""
    cls = classify(bool(v.modelV2.big), float(m.modelDataV2SP.bigLatencyMs), warmup, link)
    counts[link or '""'] = counts.get(link or '""', 0) + 1
    frames.append((cls, float(m.modelDataV2SP.bigLatencyMs),
                   float(m.modelDataV2SP.cameraToModelMs), float(v.modelV2.frameDropPerc)))

  try:
    while True:
      tick()
      now = time.monotonic()
      if now - t0 >= duration_s > 0:
        break
      if interval_s > 0 and now - last_report >= interval_s:
        print(summarize(frames, pair_miss, now - t0, counts), flush=True)
        last_report = now
  except KeyboardInterrupt:
    pass
  print(summarize(frames, pair_miss, time.monotonic() - t0, counts))


def selftest():
  assert classify(True, 20.0, False, "connected") == "ontime"
  assert classify(False, 20.0, False, "connected") == "zeropair"
  assert classify(False, 0.0, False, "connected") == "timeout"
  assert classify(False, 0.0, True, "connected") == "warmup"
  assert classify(False, 0.0, False, "connecting") == "reconnect"
  assert classify(False, 0.0, False, "lost") == "reconnect"
  assert classify(True, 20.0, False, "") == "reconnect"  # 没连上不计超时
  assert count_streaks([]) == 0
  assert count_streaks([1, 1, 1, 0, 1, 1, 1, 1]) == 2
  assert count_streaks([1, 1]) == 0
  print("selftest OK")


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument("--duration", type=float, default=0.0, help="跑多少秒；0 = 不限时")
  parser.add_argument("--interval", type=float, default=60.0, help="滚动报表间隔秒；0 = 只出终表")
  parser.add_argument("--selftest", action="store_true")
  args = parser.parse_args()
  if args.selftest:
    selftest()
  else:
    run(args.duration, args.interval)
