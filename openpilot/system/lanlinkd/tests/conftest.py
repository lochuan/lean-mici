"""Make the lanlinkd assembly layer testable off-device.

``openpilot.common.params`` dlopens ``libparams_c`` at import time, which only
exists on comma hardware (and on machines that can run a full scons build).
That made ``lanlinkd.py`` — the HTTP surface, including every authorization
check — the one module nobody could test on a dev machine.

The daemon only ever uses ``Params`` through the duck-typed interface the pure
modules already assume (``get``/``put``/``all_keys``/``get_type``/...), so we
install a stub module when the real library is absent. On a device, or anywhere
the real library builds, the genuine module is used untouched.
"""
import ctypes
import sys
import types
from pathlib import Path


def _real_params_is_loadable() -> bool:
  suffix = ".dylib" if sys.platform == "darwin" else ".so"
  lib = Path(__file__).resolve().parents[3] / "common" / f"libparams_c{suffix}"
  if not lib.is_file():
    return False
  try:
    ctypes.CDLL(str(lib))
  except OSError:
    return False
  return True


def _install_params_stub() -> None:
  """Register a minimal ``openpilot.common.params`` that needs no C library."""
  if "openpilot.common.params" in sys.modules:
    return

  module = types.ModuleType("openpilot.common.params")

  class UnknownKeyName(Exception):
    pass

  class Params:
    """In-memory stand-in. Tests always monkeypatch over this instance, so it
    only needs to be constructible and honest about unknown keys."""

    def __init__(self, d: str | None = None):
      self._values: dict[str, object] = {}

    def get(self, key, *args, **kwargs):
      return self._values.get(key.decode() if isinstance(key, bytes) else key)

    def get_bool(self, key, *args, **kwargs):
      return bool(self.get(key))

    def put(self, key, value, **kwargs):
      self._values[key.decode() if isinstance(key, bytes) else key] = value

    def put_bool(self, key, value, **kwargs):
      self.put(key, bool(value))

    def remove(self, key):
      self._values.pop(key.decode() if isinstance(key, bytes) else key, None)

    def all_keys(self):
      return [k.encode() for k in self._values]

    def get_type(self, key):
      return "STRING"

    def check_key(self, key):
      raise UnknownKeyName(key)

  module.Params = Params
  module.UnknownKeyName = UnknownKeyName
  module.ParamKeyFlag = types.SimpleNamespace(PERSISTENT=1, CLEAR_ON_MANAGER_START=2)
  module.ParamKeyType = types.SimpleNamespace(STRING=0, BOOL=1, INT=2, FLOAT=3, TIME=4, JSON=5, BYTES=6)
  sys.modules["openpilot.common.params"] = module


if not _real_params_is_loadable():
  _install_params_stub()
