from openpilot.system.lanlinkd import toyota_diag_api
from openpilot.system.lanlinkd.toyota.session import SessionError


class FakeSession:
  def __init__(self, enter_error: SessionError | None = None):
    self.enter_error = enter_error
    self.exited = False

  def snapshot(self):
    return {"state": "idle", "idleTimeoutS": 900}

  def enter(self):
    if self.enter_error:
      raise self.enter_error

  def exit(self):
    self.exited = True


def make_api(session):
  return toyota_diag_api.ToyotaDiagApi(session, backup_dir="/nonexistent")


class TestSessionEndpoints:
  def test_get_returns_snapshot(self):
    assert make_api(FakeSession()).session_get() == (200, {"state": "idle", "idleTimeoutS": 900})

  def test_enter_success_returns_new_snapshot(self):
    assert make_api(FakeSession()).session_enter() == (200, {"state": "idle", "idleTimeoutS": 900})

  def test_enter_failure_maps_status_and_message(self):
    api = make_api(FakeSession(SessionError(409, "车辆行驶中")))
    assert api.session_enter() == (409, {"error": "车辆行驶中"})

  def test_exit_is_204(self):
    session = FakeSession()
    assert make_api(session).session_exit() == (204, None)
    assert session.exited


class TestPandadRunning:
  def test_native_or_python_pandad_counts(self):
    assert toyota_diag_api.pandad_running(run=lambda cmd: cmd[0] == "pidof")
    assert toyota_diag_api.pandad_running(run=lambda cmd: cmd[0] == "pgrep")
    assert not toyota_diag_api.pandad_running(run=lambda cmd: False)
