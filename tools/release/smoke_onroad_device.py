#!/usr/bin/env python3
"""On-device smoke test: fake-car harness (BLOCK pandad, fake pandaStates+can),
launch manager, verify no managed process dies for the whole window.

Catches the class of bug that shipped on 2026-09-09, where tombstoned, modeld,
controlsd and selfdrived each exited 1 within seconds of going onroad and the
UI only showed a generic "sunnypilot Unavailable / Waiting to start".

Scope/limits: the fake CAN traffic cannot satisfy card's fingerprinting, so
card never publishes carState and selfdrived never initializes. selfdriveState
is therefore reported but NOT used as a pass criterion. This test verifies
process liveness, not end-to-end control. Real-car validation is still required.
"""
import os
import sys
import time
import signal
import subprocess
import threading

os.environ['BLOCK'] = 'pandad'
os.environ['AGNOS_VERSION'] = os.environ.get('AGNOS_VERSION', '19.7')

from cereal import messaging
from openpilot.common.realtime import Ratekeeper


def fake_car(stop_evt):
  pm = messaging.PubMaster(['pandaStates', 'peripheralState', 'can'])
  rk = Ratekeeper(100)
  while not stop_evt.is_set():
    now = int(time.monotonic() * 1e9)
    if rk.frame % 50 == 0:
      # pandaStates @2Hz with ignition
      msg = messaging.new_message('pandaStates', 1)
      ps = msg.pandaStates[0]
      ps.ignitionLine = True
      ps.pandaType = 3  # dos
      ps.safetyModel = 5  # toyota
      ps.safetyParam = 0
      ps.faultStatus = 0
      pm.send('pandaStates', msg)
      # peripheralState @2Hz
      msg = messaging.new_message('peripheralState')
      msg.peripheralState.pandaType = 3
      msg.peripheralState.voltage = 12000
      msg.peripheralState.current = 500
      msg.peripheralState.fanSpeedRpm = 1000
      pm.send('peripheralState', msg)
    # can @100Hz dummy frames (keep card's CAN parser fed)
    msg = messaging.new_message('can', 1)
    msg.can[0].address = 0x170
    msg.can[0].dat = b'\x00' * 8
    msg.can[0].src = 0
    pm.send('can', msg)
    rk.keep_time()
    _ = now


def main():
  duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60

  stop_evt = threading.Event()
  t = threading.Thread(target=fake_car, args=(stop_evt,), daemon=True)
  t.start()

  env = dict(os.environ)
  # mimic launch_chffrplus.sh for the manager children
  env['PYTHONPATH'] = '/data/openpilot:/data/openpilot/openpilot:/data/pydeps'
  mgr = subprocess.Popen([sys.executable, 'manager.py'],
                         cwd='/data/openpilot/openpilot/system/manager', env=env)

  sm = messaging.SubMaster(['selfdriveState', 'managerState', 'deviceState', 'pandaStates'])
  start = time.monotonic()
  last_ss = None
  max_gap = 0.0
  ss_count = 0
  ps_count = 0
  crashes = {}
  saw_manager_state = False
  try:
    while time.monotonic() - start < duration:
      sm.update(1000)
      now = time.monotonic() - start
      if sm.updated['pandaStates']:
        ps_count += 1
      if ps_count and ps_count % 10 == 0:
        ds = sm['deviceState']
        print(f"DBG t={now:.0f} pandaStates={ps_count} started={ds.started}", flush=True)
        ps_count += 1
      if sm.updated['selfdriveState']:
        if last_ss is not None:
          max_gap = max(max_gap, now - last_ss)
        last_ss = now
        ss_count += 1
      if sm.updated['managerState']:
        saw_manager_state = True
        for p in sm['managerState'].processes:
          if p.exitCode not in (0, None) and p.name not in crashes:
            crashes[p.name] = p.exitCode
            print(f"CRASH: {p.name} exitCode={p.exitCode} at t={now:.1f}", flush=True)
  finally:
    stop_evt.set()
    mgr.send_signal(signal.SIGINT)
    try:
      mgr.wait(timeout=20)
    except subprocess.TimeoutExpired:
      mgr.kill()

  print(f"\n=== RESULT ({duration}s) ===")
  print(f"crashes: {crashes if crashes else 'NONE'}")
  print(f"selfdriveState msgs: {ss_count}, max gap: {max_gap:.2f}s (informational only, see note)")
  if not ss_count:
    print("note: carState needs real CAN fingerprinting, which the fake harness cannot do,")
    print("      so selfdrived never initializes here. Not a pass criterion.")

  # Pass criteria: every managed process must stay up for the whole window.
  # selfdriveState is deliberately NOT a criterion -- card fingerprints the car
  # off real CAN traffic, so it never publishes carState under the fake harness
  # and selfdrived blocks in recv_one(carState) forever. Gating on it would make
  # this test fail unconditionally, which is how the 2026-09-09 FAIL got ignored.
  ok = not crashes and saw_manager_state
  if not saw_manager_state:
    print("FAIL: never received managerState; manager did not come up")
  print("SMOKE:", "PASS" if ok else "FAIL")
  return 0 if ok else 1


if __name__ == '__main__':
  sys.exit(main())
