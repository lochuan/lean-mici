"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
ASSOC_GATE = 2.0        # m, association distance gate (also filters fast oncoming objects)
MIN_TRACK_FRAMES = 2    # detections before a track is published
MAX_MISSES = 2          # frames a track survives without a detection (0.4 s at 5 Hz)
ALPHA = 0.5             # position smoothing
BETA = 0.15             # velocity correction


class VRUTracker:
  def __init__(self):
    self.tracks: dict[int, dict] = {}
    self._next_id = 0

  def reset(self) -> None:
    self.tracks = {}
    self._next_id = 0

  def update(self, detections) -> list[dict]:
    assigned: set[int] = set()
    associable = list(self.tracks.items())
    new_tracks: dict[int, dict] = {}
    for det in detections:
      best_tid, best_d = None, ASSOC_GATE
      for tid, tr in associable:
        if tid in assigned or tr['classId'] != det.classId:
          continue
        d = ((tr['x'] - det.x) ** 2 + (tr['y'] - det.y) ** 2) ** 0.5
        if d < best_d:
          best_tid, best_d = tid, d
      if best_tid is None:
        new_tracks[self._next_id] = {'classId': det.classId, 'score': det.score,
                                     'x': float(det.x), 'y': float(det.y), 'vx': 0.0, 'vy': 0.0,
                                     'hits': 1, 'misses': 0}
        self._next_id += 1
      else:
        assigned.add(best_tid)
        tr = self.tracks[best_tid]
        pred_x = tr['x'] + tr['vx']
        pred_y = tr['y'] + tr['vy']
        rx, ry = det.x - pred_x, det.y - pred_y
        tr['x'] = pred_x + ALPHA * rx
        tr['y'] = pred_y + ALPHA * ry
        tr['vx'] = tr['vx'] + BETA * rx
        tr['vy'] = tr['vy'] + BETA * ry
        tr['score'] = det.score
        tr['hits'] += 1
        tr['misses'] = 0
    self.tracks.update(new_tracks)

    for tid in list(self.tracks.keys()):
      if tid not in assigned:
        self.tracks[tid]['misses'] += 1
        if self.tracks[tid]['misses'] > MAX_MISSES:
          del self.tracks[tid]

    return [{'classId': tr['classId'], 'score': tr['score'], 'x': tr['x'], 'y': tr['y'],
             'vx': tr['vx'], 'trackId': tid}
            for tid, tr in self.tracks.items() if tr['hits'] >= MIN_TRACK_FRAMES and tr['misses'] == 0]
