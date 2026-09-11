#!/usr/bin/env python3
import argparse
import atexit
import math
import os
import tempfile
import time
import shutil
from functools import partial
from collections import namedtuple

import numpy as np

from openpilot.selfdrive.modeld.helpers import dump_oob, load_oob

def _patch_tinygrad_fetch_fw():
  import hashlib
  import pathlib
  import zstandard
  from tinygrad import helpers
  _orig = helpers.fetch_fw
  def fetch_fw(path, name, sha256):
    p = pathlib.Path(f"/lib/firmware/{path}/{name}.zst")
    if p.is_file():
      blob = zstandard.ZstdDecompressor().stream_reader(p.read_bytes()).read()
      if hashlib.sha256(blob).hexdigest() == sha256:
        return blob
    return _orig(path, name, sha256)
  helpers.fetch_fw = fetch_fw
_patch_tinygrad_fetch_fw()


from tinygrad.tensor import Tensor
from tinygrad.helpers import Context
from tinygrad.device import Device
from tinygrad.engine.jit import TinyJit


NV12Frame = namedtuple("NV12Frame", ['width', 'height', 'stride', 'y_height', 'uv_height', 'size'])
MODELD_INPUTS = ['img_q', 'big_img_q', 'feat_q', 'desire_q', 'packed_npy_inputs']

# Split models (vision + policy compiled separately) expose two JITs instead of a
# single fused run_model: a per-camera-resolution warp and a shared run_policy.
WARP_INPUTS = ['tfm', 'big_tfm']
POLICY_INPUTS = ['img_q', 'big_img_q', 'feat_q', 'desire_q', 'packed_npy_inputs']


def nv12_copy_size(stride: int, y_height: int, uv_height: int) -> int:
  # Retain the padded Y and UV plane storage, but skip the trailing kernel/guard allocation.
  return stride * (y_height + uv_height)


def warp_perspective_tinygrad(src_flat, M_inv, dst_shape, src_shape, stride_pad, border_fill_val=None):
  w_dst, h_dst = dst_shape
  h_src, w_src = src_shape

  x = Tensor.arange(w_dst).reshape(1, w_dst).expand(h_dst, w_dst).reshape(-1)
  y = Tensor.arange(h_dst).reshape(h_dst, 1).expand(h_dst, w_dst).reshape(-1)

  # inline 3x3 matmul as elementwise to avoid reduce op (enables fusion with gather)
  src_x = M_inv[0, 0] * x + M_inv[0, 1] * y + M_inv[0, 2]
  src_y = M_inv[1, 0] * x + M_inv[1, 1] * y + M_inv[1, 2]
  src_w = M_inv[2, 0] * x + M_inv[2, 1] * y + M_inv[2, 2]

  src_x = src_x / src_w
  src_y = src_y / src_w

  x_round = Tensor.round(src_x)
  y_round = Tensor.round(src_y)
  x_nn_clipped = x_round.clip(0, w_src - 1).cast('int')
  y_nn_clipped = y_round.clip(0, h_src - 1).cast('int')
  idx = y_nn_clipped * (w_src + stride_pad) + x_nn_clipped
  sampled = src_flat[idx]

  if border_fill_val is None:
    return sampled

  in_bounds = ((x_round >= 0) & (x_round <= w_src - 1) &
               (y_round >= 0) & (y_round <= h_src - 1)).cast(sampled.dtype)
  return sampled * in_bounds + Tensor(border_fill_val, dtype=sampled.dtype) * (1 - in_bounds)


def frames_to_tensor(frames):
  H = (frames.shape[0] * 2) // 3
  W = frames.shape[1]
  in_img1 = Tensor.cat(frames[0:H:2, 0::2],
                       frames[1:H:2, 0::2],
                       frames[0:H:2, 1::2],
                       frames[1:H:2, 1::2],
                       frames[H:H+H//4].reshape((H//2, W//2)),
                       frames[H+H//4:H+H//2].reshape((H//2, W//2)), dim=0).reshape((6, H//2, W//2))
  return in_img1


def make_frame_prepare(nv12: NV12Frame, model_w, model_h):
  cam_w, cam_h, stride, y_height, uv_height, _ = nv12
  uv_offset = stride * y_height
  stride_pad = stride - cam_w

  def frame_prepare_tinygrad(input_frame, M_inv):
    # UV_SCALE @ M_inv @ UV_SCALE_INV simplifies to elementwise scaling
    M_inv_uv = M_inv * Tensor([[1.0, 1.0, 0.5], [1.0, 1.0, 0.5], [2.0, 2.0, 1.0]], device=Device.DEFAULT)
    # deinterleave NV12 UV plane (UVUV... -> separate U, V)
    uv = input_frame[uv_offset:uv_offset + uv_height * stride].reshape(uv_height, stride)
    with Context(SPLIT_REDUCEOP=0):
      y = warp_perspective_tinygrad(input_frame[:cam_h*stride],
                                    M_inv, (model_w, model_h),
                                    (cam_h, cam_w), stride_pad).realize()
      u = warp_perspective_tinygrad(uv[:cam_h//2, :cam_w:2].flatten(),
                                    M_inv_uv, (model_w//2, model_h//2),
                                    (cam_h//2, cam_w//2), 0).realize()
      v = warp_perspective_tinygrad(uv[:cam_h//2, 1:cam_w:2].flatten(),
                                    M_inv_uv, (model_w//2, model_h//2),
                                    (cam_h//2, cam_w//2), 0).realize()
    yuv = y.cat(u).cat(v).reshape((model_h * 3 // 2, model_w))
    tensor = frames_to_tensor(yuv)
    return tensor
  return frame_prepare_tinygrad


def _detect_desire_key(shapes):
  """Supercombo calls it 'desire_pulse', split models call it 'desire'."""
  return next((key for key in shapes if key.startswith('desire')), None)


def _detect_vision_keys(shapes):
  """Return (road_key, wide_key) for the narrow and wide camera inputs."""
  img_keys = sorted(key for key in shapes if 'img' in key)
  return (
    next((key for key in img_keys if 'big' not in key), None),
    next((key for key in img_keys if 'big' in key), None),
  )


def derive_frame_skip(vision_input_shapes, policy_input_shapes):
  """Models carrying a full-rate features buffer (>=99 entries) run without temporal skipping."""
  features_buffer = policy_input_shapes.get('features_buffer')
  return 1 if not features_buffer or features_buffer[1] >= 99 else 4


def get_policy_npy_shapes(input_shapes, is_supercombo=False):
  # Ordering matters: run_model/run_policy split the packed buffer positionally.
  # For supercombo this yields desire, traffic_convention, action_t, prev_feat.
  desire_key = _detect_desire_key(input_shapes)
  if desire_key is None:
    raise ValueError("Desire key missing from input shapes.")

  shapes = {'desire': (input_shapes[desire_key][2],)}
  for key, shape in input_shapes.items():
    if key != desire_key and key != 'features_buffer' and 'img' not in key:
      shapes[key] = tuple(shape)

  # TODO prev_feat shouldn't exist and be handled inside the JIT, but corrupt on QCOM for now
  if is_supercombo and 'features_buffer' in input_shapes:
    fb = input_shapes['features_buffer']  # (1, T-1, ...) e.g. (1, 24, 32, 512) with spatial features
    shapes['prev_feat'] = (fb[0], math.prod(fb[2:]))

  return shapes, [math.prod(s) for s in shapes.values()]


def generate_queues_and_npy(input_shapes, frame_skip, device=None, is_supercombo=False):
  """Allocate the runtime input queues and the numpy views used to refill them.

  Unlike make_input_queues (supercombo, frames packed into one buffer for the
  fused run_model JIT), split models warp frames separately, so no frame views
  are returned here and tfm/big_tfm are exposed as their own NPY tensors.
  """
  device = Device.DEFAULT if device is None else device

  road_key, _ = _detect_vision_keys(input_shapes)
  if not road_key:
    raise ValueError("Vision road key missing from input shapes.")
  img_shape = input_shapes[road_key]
  n_frames = img_shape[1] // 6
  img_buf_shape = (frame_skip * (n_frames - 1) + 1, 6, img_shape[2], img_shape[3])

  desire_key = _detect_desire_key(input_shapes)
  if not desire_key:
    raise ValueError("Desire key missing from input shapes.")
  desire_shape = input_shapes[desire_key]

  npy_arrays = {'tfm': np.zeros((3, 3), dtype=np.float32), 'big_tfm': np.zeros((3, 3), dtype=np.float32)}

  shapes, sizes = get_policy_npy_shapes(input_shapes, is_supercombo=is_supercombo)
  packed_npy_inputs = np.zeros(sum(sizes), dtype=np.float32)
  split_views = np.split(packed_npy_inputs, np.cumsum(sizes[:-1]))
  for (k, s), v in zip(shapes.items(), split_views, strict=True):
    npy_arrays[k] = v.reshape(s)

  queues = {
    'img_q': Tensor(np.zeros(img_buf_shape, dtype=np.uint8), device=device).contiguous().realize(),
    'big_img_q': Tensor(np.zeros(img_buf_shape, dtype=np.uint8), device=device).contiguous().realize(),
    'desire_q': Tensor(np.zeros((frame_skip * desire_shape[1], desire_shape[0], desire_shape[2]),
                                dtype=np.float32), device=device).contiguous().realize(),
    'packed_npy_inputs': Tensor(packed_npy_inputs, device='NPY').realize(),
  }

  if (features_buffer := input_shapes.get('features_buffer')) is not None:
    feat_dim = math.prod(features_buffer[2:])
    # Supercombo feeds prev_feat explicitly; split models append the current frame's
    # feature inside the JIT, so they need one extra slot instead of a full stride.
    feat_q_len = frame_skip * features_buffer[1] if is_supercombo else frame_skip * (features_buffer[1] - 1) + 1
    queues['feat_q'] = Tensor(np.zeros((feat_q_len, features_buffer[0], feat_dim),
                                       dtype=np.float32), device=device).contiguous().realize()

  queues.update({k: Tensor(npy_arrays[k], device='NPY').realize() for k in WARP_INPUTS})
  return queues, npy_arrays


def make_split_input_queues(vision_input_shapes, policy_input_shapes, frame_skip, device=None):
  return generate_queues_and_npy({**vision_input_shapes, **policy_input_shapes}, frame_skip, device, is_supercombo=False)


def make_supercombo_input_queues(input_shapes, frame_skip, device=None):
  return generate_queues_and_npy(input_shapes, frame_skip, device, is_supercombo=True)


def make_input_queues(input_shapes, frame_skip, device, frame_copy_size):
  img = input_shapes['img']  # (1, 12, 128, 256)
  fb = input_shapes['features_buffer']  # (1, T-1, ...), past features only; the model appends the current frame's feature
  feat_dim = math.prod(fb[2:])
  dp = input_shapes['desire_pulse']  # (1, 25, 8)
  n_frames = img[1] // 6
  img_buf_shape = (frame_skip * (n_frames - 1) + 1, 6, img[2], img[3])

  policy_shapes, _ = get_policy_npy_shapes(input_shapes, is_supercombo=True)
  shapes = {'tfm': (3, 3), 'big_tfm': (3, 3)} | policy_shapes
  sizes = [math.prod(s) for s in shapes.values()]
  packed_npy_size = sum(sizes) * np.dtype(np.float32).itemsize
  packed_input = np.zeros(packed_npy_size + 2 * frame_copy_size, dtype=np.uint8)
  packed_npy_inputs = packed_input[:packed_npy_size].view(np.float32)
  frames = packed_input[packed_npy_size:]
  frame_views = {'img': frames[:frame_copy_size], 'big_img': frames[frame_copy_size:]}
  # views into the packed inputs, to be refilled at runtime
  npy = {k: v.reshape(s) for (k, s), v in zip(shapes.items(), np.split(packed_npy_inputs, np.cumsum(sizes[:-1])), strict=True)}
  input_queues = {
    'img_q': Tensor(np.zeros(img_buf_shape, dtype=np.uint8), device=device).contiguous().realize(),
    'big_img_q': Tensor(np.zeros(img_buf_shape, dtype=np.uint8), device=device).contiguous().realize(),
    'feat_q': Tensor(np.zeros((frame_skip * fb[1], fb[0], feat_dim), dtype=np.float32), device=device).contiguous().realize(),
    'desire_q': Tensor(np.zeros((frame_skip * dp[1], dp[0], dp[2]), dtype=np.float32), device=device).contiguous().realize(),
    'packed_npy_inputs': Tensor(packed_input, device='NPY').realize(),
  }
  return input_queues, npy, frame_views


def shift_and_sample(buf, new_val, sample_fn):
  buf.assign(buf[1:].cat(new_val, dim=0).contiguous())
  return sample_fn(buf)


def sample_skip(buf, frame_skip):
  return buf[::frame_skip].contiguous().flatten(0, 1).unsqueeze(0)


def sample_desire(buf, frame_skip):
  return buf.reshape(-1, frame_skip, *buf.shape[1:]).max(1).flatten(0, 1).unsqueeze(0)


def make_warp(nv12, model_w, model_h):
  frame_prepare = make_frame_prepare(nv12, model_w, model_h)

  def warp(tfm, big_tfm, frame, big_frame):
    tfm = tfm.to(Device.DEFAULT)
    big_tfm = big_tfm.to(Device.DEFAULT)
    frame = frame.to(Device.DEFAULT)
    big_frame = big_frame.to(Device.DEFAULT)
    Tensor.realize(tfm, big_tfm, frame, big_frame)

    warped_frame = frame_prepare(frame, tfm).unsqueeze(0)
    warped_big_frame = frame_prepare(big_frame, big_tfm).unsqueeze(0)
    return Tensor.cat(warped_frame, warped_big_frame)

  return warp


def make_run_policy(model_runner, model_metadata, frame_skip):
  sample_desire_fn = partial(sample_desire, frame_skip=frame_skip)
  sample_skip_fn = partial(sample_skip, frame_skip=frame_skip)
  npy_shapes, npy_sizes = get_policy_npy_shapes(model_metadata['input_shapes'], is_supercombo=True)
  model_input_dtypes = {name: spec.dtype for name, spec in model_runner.graph_inputs.items()}

  def run_policy(warped, img_q, big_img_q, feat_q, desire_q, packed_npy_inputs):
    packed_npy_inputs = packed_npy_inputs.to(Device.DEFAULT)
    Tensor.realize(packed_npy_inputs, warped)

    img = shift_and_sample(img_q, warped[0:1], sample_skip_fn)
    big_img = shift_and_sample(big_img_q, warped[1:2], sample_skip_fn)

    desire, traffic_convention, action_t, prev_feat = (t.reshape(s) for t, s in zip(packed_npy_inputs.split(npy_sizes), npy_shapes.values(), strict=True))
    desire_buf = shift_and_sample(desire_q, desire.reshape(1, 1, -1), sample_desire_fn)
    feat_buf = shift_and_sample(feat_q, prev_feat.reshape(1, 1, -1), sample_skip_fn)

    inputs = {
      'img': img,
      'big_img': big_img,
      'features_buffer': feat_buf.reshape(model_metadata['input_shapes']['features_buffer']),
      'desire_pulse': desire_buf,
      'traffic_convention': traffic_convention,
      'action_t': action_t,
    }
    inputs = {name: value.cast(model_input_dtypes[name]) for name, value in inputs.items()}
    out = next(iter(model_runner(inputs).values())).cast('float32')
    return out,
  return run_policy


def make_run_model(warp, run_policy, model_metadata, frame_copy_size):
  _, policy_sizes = get_policy_npy_shapes(model_metadata['input_shapes'], is_supercombo=True)
  packed_npy_size = (18 + sum(policy_sizes)) * np.dtype(np.float32).itemsize

  def run_model(img_q, big_img_q, feat_q, desire_q, packed_npy_inputs):
    packed_input = packed_npy_inputs.to(Device.DEFAULT)
    Tensor.realize(packed_input)
    packed_npy_inputs = packed_input[:packed_npy_size].bitcast('float32')
    frame = packed_input[packed_npy_size:packed_npy_size + frame_copy_size]
    big_frame = packed_input[packed_npy_size + frame_copy_size:]
    tfm, big_tfm, policy_inputs = packed_npy_inputs.split([9, 9, sum(policy_sizes)])
    warped = warp(tfm.reshape(3, 3), big_tfm.reshape(3, 3), frame, big_frame)
    return run_policy(warped, img_q, big_img_q, feat_q, desire_q, policy_inputs)
  return run_model


def compile_jit(jit, input_keys, make_queues, benchmark_runs):
  if benchmark_runs < 1:
    raise ValueError("benchmark_runs must be at least 1")

  SEED = 42
  def random_inputs_run(fn, seed, n_runs, test_val=None, test_buffers=None, expect_match=True):
    input_queues, npy, frame_views = make_queues(Device.DEFAULT)
    rng = np.random.default_rng(seed)

    for i in range(n_runs):
      for v in npy.values():
        v[:] = rng.standard_normal(v.shape).astype(v.dtype)
      for v in frame_views.values():
        v[:] = rng.integers(0, 256, size=v.shape, dtype=np.uint8)
      Device.default.synchronize()
      st = time.perf_counter()
      outs = fn(**{k: input_queues[k] for k in input_keys})
      mt = time.perf_counter()
      Device.default.synchronize()
      et = time.perf_counter()
      print(f"  [{i+1}/{n_runs}] enqueue {(mt-st)*1e3:6.2f} ms -- total {(et-st)*1e3:6.2f} ms")

      if i == 0:
        val = [np.copy(v.numpy()) for v in outs]
        buffers = [np.copy(v.numpy().copy()) for v in input_queues.values()]

    if test_val is not None:
      match = all(np.array_equal(a, b) for a, b in zip(val, test_val, strict=True))
      assert match == expect_match, f"outputs {'differ from' if expect_match else 'match'} baseline (seed={seed})"
    if test_buffers is not None:
      match = all(np.array_equal(a, b) for a, b in zip(buffers, test_buffers, strict=True))
      assert match == expect_match, f"buffers {'differ from' if expect_match else 'match'} baseline (seed={seed})"
    return val, buffers

  print('capture + replay')
  test_val, test_buffers = random_inputs_run(jit, SEED, 3)
  print(f'pickle round trip ({benchmark_runs} runs per seed)')
  with tempfile.TemporaryFile(dir=".") as f:
    dump_oob(jit, f)
    f.seek(0)
    loaded_jit = load_oob(f)
  random_inputs_run(loaded_jit, SEED, benchmark_runs, test_val, test_buffers, expect_match=True)
  random_inputs_run(loaded_jit, SEED+1, benchmark_runs, test_val, test_buffers, expect_match=False)
  # Keep the original so per-resolution JITs share model weight buffers in the final pickle.
  return jit


def _parse_size(s):
  w, h = s.lower().split('x')
  return int(w), int(h)


def read_file_chunked_to_disk(path):
  from openpilot.common.file_chunker import open_file_chunked
  tmp_path = f'{path}.unchunked'
  with open(tmp_path, 'wb') as f, open_file_chunked(path) as src:
    shutil.copyfileobj(src, f)
  atexit.register(lambda: os.path.exists(tmp_path) and os.remove(tmp_path))
  return tmp_path


if __name__ == "__main__":
  from tinygrad.nn.onnx import OnnxRunner
  from openpilot.system.camerad.cameras.nv12_info import get_nv12_info
  from openpilot.selfdrive.modeld.get_model_metadata import make_metadata_dict
  p = argparse.ArgumentParser()
  p.add_argument('--model-size', type=_parse_size, required=True, help='model input WxH')
  p.add_argument('--camera-resolutions', type=_parse_size, nargs='+', required=True,
                 help='camera resolutions WxH (one or more)')
  p.add_argument('--onnx', required=True)
  p.add_argument('--output', required=True)
  p.add_argument('--frame-skip', type=int, required=True)
  p.add_argument('--benchmark-runs', type=int, default=1,
                 help='timed loaded-JIT runs for each correctness seed')
  args = p.parse_args()

  model_path = read_file_chunked_to_disk(args.onnx)
  model_w, model_h = args.model_size

  model_runner = OnnxRunner(model_path)
  out = {
    'metadata': make_metadata_dict(model_path),
    'input_devices': {'model': Device.DEFAULT},
    'run_model': {},
  }

  run_policy = make_run_policy(model_runner, out['metadata'], args.frame_skip)

  for cam_w, cam_h in args.camera_resolutions:
    nv12 = NV12Frame(cam_w, cam_h, *get_nv12_info(cam_w, cam_h))
    frame_copy_size = nv12_copy_size(nv12.stride, nv12.y_height, nv12.uv_height)
    make_model_queues = partial(make_input_queues, out['metadata']['input_shapes'], args.frame_skip,
                                frame_copy_size=frame_copy_size)
    warp = make_warp(nv12, model_w, model_h)
    run_model_jit = TinyJit(make_run_model(warp, run_policy, out['metadata'], frame_copy_size), prune=True)
    out['run_model'][(cam_w,cam_h)] = compile_jit(run_model_jit, MODELD_INPUTS, make_model_queues,
                                                  args.benchmark_runs)

  with open(args.output, "wb") as f:
    dump_oob(out, f)
  print(f"Saved JITs to {args.output} ({os.path.getsize(args.output) / 1e6:.2f} MB)")
