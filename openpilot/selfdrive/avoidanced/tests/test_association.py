"""Tests for radar <-> vision association (core shared with the shadow harness)."""

import pytest

from openpilot.selfdrive.avoidanced.association import ASSOC_MAX_DX, ASSOC_MAX_DY, associate, nearest_pairs
from openpilot.selfdrive.avoidanced.shadow import RadarTarget, VisionObject, associate as shadow_associate


def _det(d_rel, y_rel, cls="person", conf=0.9):
  return {"dRel": d_rel, "yRel": y_rel, "cls": cls, "conf": conf}


def test_associate_matches_nearest_within_gates():
  radar = [RadarTarget(10.0, -1.0)]
  dets = [_det(10.4, -1.2), _det(30.0, 0.0, cls="car")]
  n, fused, pairs = associate(radar, dets)
  assert n == 1
  # The matched detection is absorbed by the radar point -> not re-added.
  assert fused == [dets[1]]
  # pair_id counts up from 1; both sides share it.
  assert len(pairs) == 1
  radar_p, vision_p, pair_id = pairs[0]
  assert radar_p is radar[0]
  assert vision_p is dets[0]
  assert pair_id == 1


def test_associate_unmatched_detection_stays_independent():
  radar = [RadarTarget(30.0, 0.0)]
  dets = [_det(10.0, -1.0, cls="bicycle")]
  n, fused, pairs = associate(radar, dets)
  assert n == 0
  assert fused == dets
  assert pairs == []


def test_associate_gates_are_dx2_dy1():
  assert ASSOC_MAX_DX == 2.0
  assert ASSOC_MAX_DY == 1.0
  radar = [RadarTarget(10.0, -1.0)]
  just_out = _det(10.0, -1.0 - ASSOC_MAX_DY - 0.01)
  n, fused, pairs = associate(radar, [just_out])
  assert n == 0
  assert fused == [just_out]
  assert pairs == []


def test_associate_nearest_wins():
  radar = [RadarTarget(10.0, 0.0)]
  dets = [_det(11.0, 0.0, cls="car", conf=0.5), _det(10.2, 0.1, conf=0.9)]
  n, fused, pairs = associate(radar, dets)
  assert n == 1
  assert fused == [dets[0]]
  assert pairs[0][1] is dets[1]


def test_associate_empty_inputs():
  assert associate([], []) == (0, [], [])
  assert associate([], [_det(1.0, 0.0)]) == (0, [_det(1.0, 0.0)], [])
  assert associate([RadarTarget(1.0, 0.0)], []) == (0, [], [])


def test_associate_pair_ids_count_up_from_one():
  radar = [RadarTarget(10.0, 0.0), RadarTarget(25.0, -1.5)]
  dets = [_det(10.5, 0.1, cls="car"), _det(26.0, -1.4)]
  n, fused, pairs = associate(radar, dets)
  assert n == 2
  assert [p[2] for p in pairs] == [1, 2]
  assert fused == []  # both detections absorbed


def test_nearest_pairs_returns_gates_residuals():
  pairs, matched = nearest_pairs([RadarTarget(10.0, -1.0)], [_det(10.4, -1.2)], 2.0, 1.0)
  assert len(pairs) == 1
  radar, obj, dx, dy = pairs[0]
  assert radar.dRel == 10.0
  assert obj["dRel"] == 10.4
  assert dx == pytest.approx(0.4)
  assert dy == pytest.approx(0.2)
  assert matched == {0}


def test_shadow_associate_reuses_core_with_its_own_gates():
  # The shadow harness keeps its wider 3.0/1.5 gates for the P0 metrics; the
  # daemon path gates tighter (2.0/1.0). Same core, no duplicated matcher.
  from openpilot.selfdrive.avoidanced import shadow

  assert shadow.ASSOC_MAX_DX == 3.0
  assert shadow.ASSOC_MAX_DY == 1.5
  pairs = shadow_associate([RadarTarget(10.0, -1.0)], [VisionObject(12.5, -2.0)])
  assert len(pairs) == 1  # within the shadow gates...
  n, _, _ = associate([RadarTarget(10.0, -1.0)], [_det(12.5, -2.0)])
  assert n == 0          # ...but outside the daemon gates
