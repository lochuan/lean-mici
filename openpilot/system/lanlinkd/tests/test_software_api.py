"""software_api 单测：状态快照组装与动作 gating（stub params，不碰 DBus/进程）。"""
import pytest

from openpilot.system.lanlinkd import software_api


class StubParams:
  def __init__(self, values=None):
    self.values = values or {}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    v = self.values.get(key)
    return bool(v) and str(v) not in ("0", "false", "False")

  def put_bool(self, key, value, block=False):
    self.values[key] = value


def _full_params(**overrides):
  base = {
    "Version": "2026.003.000",
    "GitBranch": "lean-release",
    "GitCommit": "abc123",
    "UpdaterCurrentDescription": "2026.003.000 / lean-release / oldcommit / Tue Sep 14",
    "UpdaterNewDescription": "2026.004.000 / lean-release / newcommit / Wed Sep 15",
    "UpdaterState": "idle",
    "UpdateAvailable": 1,
    "UpdaterFetchAvailable": 1,
    "UpdateFailedCount": 0,
    "UpdaterTargetBranch": "lean-release",
    "UpdaterAvailableBranches": "lean-release,devel",
    "IsOffroad": 1,
  }
  base.update(overrides)
  return StubParams(base)


class TestStatus:
  def test_full_payload(self, monkeypatch):
    monkeypatch.setattr(software_api, "_remote_commit", lambda p: "669b1769b4ba80c1a7843ff35a7bbf9e5c06f569")
    st = software_api.status(_full_params())
    assert st["version"] == "2026.003.000"
    assert st["branch"] == "lean-release"
    assert st["current"]["version"] == "2026.003.000"
    assert st["updaterState"] == "idle"
    assert st["updateAvailable"] is True
    assert st["newVersion"] and st["newVersion"]["version"] == "2026.004.000"
    assert st["availableBranches"] == ["lean-release", "devel"]
    assert st["remoteCommit"] == "669b1769b4ba80c1a7843ff35a7bbf9e5c06f569"

  def test_remote_commit_git_failure_is_empty(self, monkeypatch):
    # git 不可用/引用不存在时必须回落为空串（前端显示 "-"），不能抛异常
    class FakeProc:
      returncode = 128
      stdout = ""
    monkeypatch.setattr(software_api.subprocess, "run", lambda *a, **k: FakeProc())
    assert software_api._remote_commit(_full_params()) == ""

  def test_remote_commit_uses_target_branch(self, monkeypatch):
    captured = {}
    def fake_run(cmd, **k):
      captured["cmd"] = cmd
      class FakeProc:
        returncode = 0
        stdout = "cafe123\n"
      return FakeProc()
    monkeypatch.setattr(software_api.subprocess, "run", fake_run)
    assert software_api._remote_commit(_full_params(UpdaterTargetBranch="devel")) == "cafe123"
    assert "origin/devel" in captured["cmd"]

  def test_empty_params(self, monkeypatch):
    monkeypatch.setattr(software_api, "_remote_commit", lambda p: "")
    st = software_api.status(StubParams({"UpdateAvailable": 0, "UpdaterCurrentDescription": "malformed"}))
    assert st["version"] == ""
    assert st["current"] is None
    assert st["updateAvailable"] is False
    assert st["remoteCommit"] == ""

  def test_failed_count_str(self):
    st = software_api.status(_full_params(UpdateFailedCount="3"))
    assert st["failed"] is True
    assert st["failedCount"] == 3


class TestSignal:
  def test_check_no_updater_503(self):
    # 本机没有 updated 进程（单测环境），check 应给明确的 503 而不是 500
    code, msg = software_api.signal(_full_params(), "check")
    assert code == 503
    assert "not running" in msg

  def test_check_onroad_409(self):
    p = _full_params(IsOffroad=0)
    code, _ = software_api.signal(p, "check")
    assert code == 409

  def test_install_without_update_409(self):
    p = _full_params(UpdateAvailable=0)
    code, _ = software_api.signal(p, "install")
    assert code == 409

  def test_install_with_update_sets_reboot(self):
    p = _full_params()
    code, _ = software_api.signal(p, "install")
    assert code == 200
    # 用 stub 的 put_bool 语义写回了一个真值
    assert software_api._DO_REBOOT_KEY in p.values and p.values[software_api._DO_REBOOT_KEY] is True

  def test_download_maps_sighup(self):
    code, _ = software_api.signal(_full_params(), "download")
    # updated 进程不存在时给 503（不 500），说明动作已被映射
    assert code == 503

  def test_unknown_action(self):
    code, _ = software_api.signal(_full_params(), "reboot")
    assert code == 404

  def test_version_sanitizer(self):
    assert software_api.sanitize_report_version("2026.003!@#.000") == "2026.003.000"
