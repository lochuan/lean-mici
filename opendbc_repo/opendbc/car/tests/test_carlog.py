"""carlog.error_once:已知无害的持续告警,进程内同 key 只报一次。

背景:SecOC 车型在 EPS bypass + 原车纵向的形态下,同步 MAC 每次计数跳变都
失配(全零假密钥),carcontroller 每跳变刷一条 error —— 一趟 25 分钟路测
8758 条(2026-09-25 用户裁定:此车不需要 SecOC,勿再刷屏)。
"""
from unittest import mock

from opendbc.car import carlog as carlog_mod


def test_error_once_sends_each_key_once():
  carlog_mod._reported_once.clear()
  with mock.patch.object(carlog_mod.carlog, "error") as err:
    carlog_mod.error_once("secoc_sync_mac", "boom")
    carlog_mod.error_once("secoc_sync_mac", "boom")
    carlog_mod.error_once("secoc_sync_mac", "boom")
    carlog_mod.error_once("other", "boom2")
    assert err.call_count == 2
    err.assert_any_call("boom")
    err.assert_any_call("boom2")


def test_error_once_survives_fresh_call_after_clear():
  carlog_mod._reported_once.clear()
  with mock.patch.object(carlog_mod.carlog, "error") as err:
    carlog_mod.error_once("k", "m")
  carlog_mod._reported_once.clear()
  with mock.patch.object(carlog_mod.carlog, "error") as err2:
    carlog_mod.error_once("k", "m")
  assert err.call_count == 1 and err2.call_count == 1
