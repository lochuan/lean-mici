"""Tinygrad revision (pin) compatibility between the device tree and model pkls.

A driving pkl embeds tinygrad JIT kernels compiled against one specific
tinygrad revision. Running a pkl under a different revision crashes modeld
(the 2026-09-20 pin bump shipped e837-built pkls that died on the 0811 tree).

Producers publish a catalog-level ``tinygrad_ref`` and the model manager
copies it into a ``<pkl>.tinygrad_pin`` sidecar at download time. Consumers
check the sidecar before loading: unknown (no sidecar) keeps the legacy load
attempt, a mismatch falls back to the built-in model instead of taking
modeld down.
"""

import os

from openpilot.sunnypilot.models.tinygrad_ref import get_tinygrad_ref

PIN_SUFFIX = ".tinygrad_pin"


def device_tinygrad_ref() -> str | None:
  """The tinygrad revision this tree runs on. None when it can't be resolved."""
  try:
    return get_tinygrad_ref() or None
  except Exception:
    return None


def read_pkl_pin(pkl_path: str) -> str | None:
  """Pin recorded by the producer for this pkl. None when absent/unreadable."""
  try:
    with open(pkl_path + PIN_SUFFIX) as f:
      return f.read().strip() or None
  except OSError:
    return None


def pkl_pin_compatible(pkl_path: str) -> bool | None:
  """True/False when a sidecar pin exists, None when unknown (no sidecar)."""
  pkl_pin = read_pkl_pin(pkl_path)
  device_ref = device_tinygrad_ref()
  if pkl_pin is None or device_ref is None:
    return None
  return pkl_pin == device_ref


def write_pkl_pin(pkl_path: str, tinygrad_ref: str) -> None:
  with open(pkl_path + PIN_SUFFIX, "w") as f:
    f.write(tinygrad_ref)
