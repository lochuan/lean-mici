import os
import subprocess
import sys

from openpilot.common.test import OpenpilotTestCase

UPDATER_ZIPAPP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../updater"))

# Runs in a clean interpreter so the repo's own openpilot package can't shadow the zipapp's.
IMPORT_HARDWARE_WITHOUT_SERIAL_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
sys.modules["serial"] = None  # a foreign AGNOS venv may lack pyserial (upstream #38925)
import openpilot.system.hardware as hardware
assert hardware.__file__.startswith(sys.argv[1]), hardware.__file__
"""


class TestUpdaterWithoutPyserial(OpenpilotTestCase):
  # Downgrading from a foreign AGNOS runs this updater on that AGNOS's venv; a crash
  # there loops forever in launch_chffrplus.sh and the device sits on the boot logo.
  # ponytail: covers only the hardware import chain (dev venv has no pyray), upgrade to importing
  # openpilot.system.ui.updater against the oldest supported AGNOS venv if another dep slips in.
  def test_hardware_imports_without_pyserial(self):
    result = subprocess.run([sys.executable, "-I", "-c", IMPORT_HARDWARE_WITHOUT_SERIAL_SCRIPT, UPDATER_ZIPAPP_PATH],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
