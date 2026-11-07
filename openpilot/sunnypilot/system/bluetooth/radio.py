import subprocess
import time

from pathlib import Path


RADIO_HELPER = "/usr/comma/bluetooth-radio"

# unit name differs between forks: our AGNOS builds ship "sunnypilot-",
# StarPilot images ship "starpilot-"
UNIT_NAMES = ("sunnypilot-bluetooth-radio.service", "starpilot-bluetooth-radio.service")



class BluetoothRadio:
  def __init__(self, helper: str = RADIO_HELPER):
    self.helper = helper

  @property
  def available(self) -> bool:
    return Path(self.helper).is_file()

  @property
  def ready(self) -> bool:
    return Path("/sys/class/bluetooth/hci0").exists()

  def _pick_unit(self) -> str | None:
    try:
      out = subprocess.check_output(["systemctl", "list-unit-files", "--no-legend", "--no-pager"], text=True, timeout=10)
    except Exception:
      return None
    for unit in UNIT_NAMES:
      if any(line.split()[0] == unit for line in out.splitlines() if line.split()):
        return unit
    return None

  def start(self, timeout: float = 50.0) -> None:
    if not self.available:
      raise RuntimeError("Bluetooth radio support is not installed")
    unit = self._pick_unit()
    if unit is None:
      raise RuntimeError("Bluetooth radio unit not found")
    subprocess.run(["sudo", "-n", "systemctl", "start", unit], check=True, timeout=timeout)
    deadline = time.monotonic() + timeout
    while not self.ready:
      if time.monotonic() >= deadline:
        raise RuntimeError("Bluetooth radio did not become ready")
      time.sleep(0.1)

  def stop(self, timeout: float = 10.0) -> None:
    if self.available:
      unit = self._pick_unit()
      if unit is not None:
        subprocess.run(["sudo", "-n", "systemctl", "stop", unit], check=True, timeout=timeout)
