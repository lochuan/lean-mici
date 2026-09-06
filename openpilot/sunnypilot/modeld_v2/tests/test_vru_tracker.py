"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.modeld_v2.vru_tracker import VRUTracker, ALPHA, BETA


class Det:
  def __init__(self, class_id, score, x, y):
    self.classId = class_id
    self.score = score
    self.x = x
    self.y = y


def drive(tracker, frames):
  out = []
  for frame in frames:
    out.append(tracker.update([Det(*f) for f in frame]))
  return out


class TestTracker(OpenpilotTestCase):
  def test_needs_two_frames(self):
    t = VRUTracker()
    (r1, r2, r3) = drive(t, [[(1, 0.9, 15.0, -2.5)], [(1, 0.9, 15.0, -2.5)], [(1, 0.9, 15.0, -2.5)]])
    assert r1 == []
    assert len(r2) == 1 and r2[0]['trackId'] == r3[0]['trackId']
    np.testing.assert_allclose([r2[0]['x'], r2[0]['y']], [15.0, -2.5], atol=1e-9)

  def test_alpha_beta_math(self):
    t = VRUTracker()
    (r2, r3) = drive(t, [[(1, 0.9, 10.0, 0.0)], [(1, 0.9, 11.0, 0.0)], [(1, 0.9, 12.0, 0.0)]])[1:]
    # f2: pred=10, r=1 -> x=10+ALPHA, v=BETA;  f3: pred=x2+v2, r=12-pred
    x2, v2 = 10.0 + ALPHA, BETA
    x3 = x2 + v2 + ALPHA * (12.0 - (x2 + v2))
    v3 = v2 + BETA * (12.0 - (x2 + v2))
    np.testing.assert_allclose([r2[0]['x'], r2[0]['vx']], [x2, v2], atol=1e-9)
    np.testing.assert_allclose([r3[0]['x'], r3[0]['vx']], [x3, v3], atol=1e-9)
    assert 0.0 < r3[0]['vx'] < 1.0

  def test_dropout_hold(self):
    t = VRUTracker()
    frames = [[(1, 0.9, 15.0, -2.5)], [(1, 0.9, 15.0, -2.5)], [], [], [(1, 0.9, 15.5, -2.5)]]
    (r1, r2, r3, r4, r5) = drive(t, frames)
    assert r3 == [] and r4 == []          # missed frames not published
    assert len(r5) == 1 and r5[0]['trackId'] == r2[0]['trackId']  # reacquired, same id

  def test_deleted_after_max_misses(self):
    t = VRUTracker()
    frames = [[(1, 0.9, 15.0, -2.5)], [(1, 0.9, 15.0, -2.5)], [], [], [], [(1, 0.9, 15.0, -2.5)], [(1, 0.9, 15.0, -2.5)]]
    (r2, _, _, _, r6, r7) = drive(t, frames)[1:]
    assert r6 == []                        # new track, hits=1
    assert len(r7) == 1 and r7[0]['trackId'] != r2[0]['trackId']

  def test_class_change_new_track(self):
    t = VRUTracker()
    (r2, r3, r4) = drive(t, [[(7, 0.9, 15.0, -2.5)], [(7, 0.9, 15.0, -2.5)], [(3, 0.9, 15.0, -2.5)], [(3, 0.9, 15.0, -2.5)]])[1:]
    assert len(r4) == 1 and r4[0]['classId'] == 3 and r4[0]['trackId'] != r2[0]['trackId']

  def test_two_targets(self):
    t = VRUTracker()
    frames = [[(1, 0.9, 15.0, -2.5), (7, 0.8, 5.0, -3.7)]] * 3
    (r2, r3) = drive(t, frames)[1:]
    assert len(r2) == 2 and len(r3) == 2
    ids2 = {d['trackId'] for d in r2}
    assert ids2 == {d['trackId'] for d in r3}

  def test_reset(self):
    t = VRUTracker()
    drive(t, [[(1, 0.9, 15.0, -2.5)]] * 2)
    t.reset()
    assert t.update([]) == []
