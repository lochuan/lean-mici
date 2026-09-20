"""全链路集成:随机场景剧本 + 语料级不变量(Task 7)。

test_daemon.py 已覆盖六个验收场景的确定性断言(弯道/跟车消失/起步/用户操作/
安全停手/参数门)。这里补的是**语料级**不变量——在固定种子随机化的长程运行里,
无论剧本如何组合,以下各条永真:

  1. 我们发出的任何命令只有 accel/decel(协议上不可能表达 CANCEL,断言把
     设计意图钉进测试,防止未来扩展按钮枚举时无声突破)
  2. 任何时刻观测 setSpeed ≤ 上限 + 一个量子(上限重同步语义的容差)
  3. 每次**发出**向上按压时 setSpeed−vEgo < 偏移帽;向下时 vEgo−setSpeed < 偏移帽
     (帽子管的是"发",不是"观测"——车追不上时观测偏移可以超帽,那正是停手的原因)
  4. 连续向上按压的时间间隔 ≥ 量子/(滑条×3.6) − 容差(滑条节奏是下界)

剧本随机化:上限起点/滑条/场景开关/前车出现·变速·消失/弯道 vTarget 进出/
用户 SET·±·CANCEL/刹车脉冲。种子固定,失败可复现。
"""
from __future__ import annotations

import random

import pytest

from opendbc.car import structs
from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.tests.test_daemon import (
  DT, Harness, Q, _Pt, VISION_ENTERING,
)

ButtonType = structs.CarState.ButtonEvent.Type
EPS = 1e-6


class _Episode:
  """一轮随机剧本。固定种子的 PRNG 驱动,不变量在运行中逐帧检查。"""

  def __init__(self, rng: random.Random, idx: int):
    self.rng = rng
    self.idx = idx
    self.accel = round(rng.uniform(0.3, 2.0), 1)
    self.h = Harness(accel=self.accel, set_speed=self._kph(60, 110),
                     v_ego=1.0)  # v_ego 由 setup 重设
    self.press_log: list[tuple[float, object, float, float]] = []  # (t, cmd, setSpeed, vEgo)
    self._patch_press()

    cs = self.h.sm._d["carState"]
    self.h.vehicle.v_ego_ms = cs.vEgo = self.h.set_speed / 3.6
    self.h.run(0.1)  # 装载 + 上限锚定

  def _kph(self, lo: float, hi: float) -> float:
    return round(self.rng.uniform(lo, hi)) * 1.0

  def _patch_press(self):
    """记录每条命令的发出时刻与当时的 setSpeed/vEgo(帽子不变量用)。"""
    h = self.h
    orig = h.actuator.press

    def recorder(cmd):
      cs = h.sm._d["carState"]
      self.press_log.append((h.now, cmd, h.vehicle.set_speed_kph, cs.vEgo * 3.6))
      orig(cmd)

    h.actuator.press = recorder

  # --- 剧本事件 ---

  def lead_event(self):
    r = self.rng
    if r.random() < 0.35 and self.h.sm._d["radarTracks"].points:
      self.h.set_env(points=())
      return
    speed_kph = self._kph(0, 100)
    pts = [_Pt(d_rel=round(r.uniform(5, 120)), lead_speed_ms=speed_kph / 3.6,
               y_rel=round(r.uniform(-1.5, 1.5), 1))]
    self.h.set_env(points=pts)

  def scc_event(self):
    if self.rng.random() < 0.5:
      vt = self._kph(25, 90)
      self.h.set_env(vision_state=VISION_ENTERING, v_target_ms=vt / 3.6)
    else:
      self.h.set_env(vision_state=0, v_target_ms=0.0)  # disabled/none

  def user_event(self):
    r = self.rng
    roll = r.random()
    if roll < 0.10:
      self.h.user_press("cancel", 100.0)
      self.h.set_env(enabled=False)   # 巡航关断路径
      return
    if roll < 0.35:
      # 用户 SET:车速附近取整设定
      new = max(30.0, self.h.sm._d["carState"].vEgo * 3.6 + r.uniform(-3, 3))
      self.h.vehicle.set_speed_kph = round(new)
      self.h.user_press("set", 100.0)
      return
    delta = round(r.uniform(1, 3)) * Q * (1 if r.random() < 0.5 else -1)
    self.h.vehicle.set_speed_kph = max(30.0, self.h.vehicle.set_speed_kph + delta)
    self.h.user_press("accel" if delta > 0 else "decel", 100.0)

  def brake_burst(self):
    self.h.set_env(brake=True)
    self.h.run(round(self.rng.uniform(0.2, 0.6), 2))
    self.h.set_env(brake=False)

  # --- 驱动与不变量 ---

  def run(self, seconds: float):
    h = self.h
    n_frames = int(seconds / DT)
    for _ in range(n_frames):
      h.run(DT)
      self._check_frame()

  def _check_frame(self):
    h = self.h
    ceiling = h.tracker.ceiling_kph
    if ceiling is not None:
      assert h.set_speed <= ceiling + Q + EPS, \
        f"ep{self.idx} t={h.now:.2f}: setSpeed {h.set_speed:.1f} > 上限 {ceiling:.1f}+q"
    for t, cmd, set_speed, v_ego_kph in self.press_log:
      if cmd.button == "accel":
        assert set_speed - v_ego_kph < C.OFFSET_CAP_KPH + EPS, \
          f"ep{self.idx} t={t:.2f}: 帽违规向上 setSpeed {set_speed:.1f} vEgo {v_ego_kph:.1f}"
      else:
        assert v_ego_kph - set_speed < C.OFFSET_CAP_KPH + EPS, \
          f"ep{self.idx} t={t:.2f}: 帽违规向下 vEgo {v_ego_kph:.1f} setSpeed {set_speed:.1f}"

  def check_corpus(self):
    ups = [(t, cmd) for t, cmd, *_ in self.press_log if cmd.button == "accel"]
    min_interval = Q / (self.accel * 3.6) - 0.02
    for (t0, c0), (t1, c1) in zip(ups, ups[1:]):
      assert t1 - t0 >= min_interval, \
        f"ep{self.idx}: 向上节奏 {t1 - t0:.3f}s < 滑条下界 {min_interval:.3f}s"
    for _, cmd, *_ in self.press_log:
      assert cmd.button in ("accel", "decel"), f"ep{self.idx}: 非法按钮 {cmd.button!r}"


def _run_episode(rng: random.Random, idx: int) -> None:
  ep = _Episode(rng, idx)
  for _ in range(int(ep.rng.uniform(6, 14))):
    pick = ep.rng.random()
    if pick < 0.30:
      ep.lead_event()
    elif pick < 0.50:
      ep.scc_event()
    elif pick < 0.80:
      ep.user_event()
    else:
      ep.brake_burst()
    ep.run(round(ep.rng.uniform(1.0, 3.0), 2))
  ep.check_corpus()


def test_random_scenario_corpus_invariants():
  """25 轮随机剧本,~2000 帧全链路:上限/偏移帽/节奏/按钮枚举四条永真。"""
  master = random.Random(20260921)
  for idx in range(25):
    _run_episode(random.Random(master.getrandbits(64)), idx)


def test_lead_speedup_follows_at_slider_rate():
  """前车提速:40→60,setSpeed 跟到 ~lead+margin,节奏 = 滑条下界。"""
  h = Harness(accel=1.0, set_speed=100.0, v_ego=100 / 3.6)
  h.run(0.5)
  # 先跟上 40 的慢车
  h.set_env(points=[_Pt(d_rel=60, lead_speed_ms=40 / 3.6)])
  h.run(25.0)
  assert h.set_speed == pytest.approx(40 + C.MARGIN_KPH, abs=1.0)
  n_before = len(h.commands)
  # 前车提速到 60
  h.set_env(points=[_Pt(d_rel=60, lead_speed_ms=60 / 3.6)])
  ups = []
  orig = h.actuator.press
  def rec(cmd):
    if cmd.button == "accel":
      ups.append(h.now)
    orig(cmd)
  h.actuator.press = rec
  h.run(12.0)
  # 死区语义:调整收敛到 |setSpeed−目标| ≤ 死区内即停,不追求精确到格
  assert abs(h.set_speed - (60 + C.MARGIN_KPH)) <= C.DEADBAND_KPH + Q
  # 节奏下界:1.0 m/s² → 每拍 ~Q/3.6 s
  gaps = [b - a for a, b in zip(ups, ups[1:])]
  assert gaps, "提速期间应发生向上按压"
  for g in gaps:
    assert g >= Q / 3.6 - 0.02, f"节奏 {g:.3f}s 快于滑条下界"
  # 帽内:每拍发出时 setSpeed − vEgo < 帽(由模糊测试逐帧覆盖,这里抽验终态)
  cs = h.sm._d["carState"]
  assert h.set_speed - cs.vEgo * 3.6 < C.OFFSET_CAP_KPH + EPS
  assert n_before < len(h.commands)
