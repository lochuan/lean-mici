"""pigeond 纯逻辑测试(硬件行为见 test_pigeond.py,仅设备上跑)。

lean trim phase 2(c81f9745d)把 pigeond 连同 common/api 一起删了——u-blox 的
串口读取器没了,ubloxd 永远等不到 ubloxRaw,gpsLocationExternal 全程为零,
noGps 事件刷屏(2026-09-25 路测 1530 次)。恢复时的边界:GNSS 配置+原始流
发布必须回来,comma 云端 Api 依赖不回来。
"""
import pytest

import openpilot.system.ubloxd.pigeond as pigeond


def test_pigeond_imports_without_cloud_api():
  """common/api 已删(lean 理念:无云端)。恢复的 pigeond 不得再引用它。"""
  assert not hasattr(pigeond, "Api")


def test_assistnow_payload_splits_into_ubx_messages():
  """AssistNow 返回的是背靠背 UBX 帧:按长度切分,残缺帧断言拒绝。"""
  m1 = b"\xB5\x62" + bytes([0x02, 0x00, 0x02, 0x00]) + b"\xAA\xBB" + b"\x11\x22"
  m2 = b"\xB5\x62" + bytes([0x03, 0x00, 0x01, 0x00]) + b"\xCC" + b"\x33\x44"
  assert pigeond.split_assistnow_messages(m1 + m2) == [m1, m2]


def test_assistnow_split_rejects_non_ubx_payload():
  with pytest.raises(AssertionError):
    pigeond.split_assistnow_messages(b"garbage!")


def test_get_assistnow_raises_without_credentials(monkeypatch):
  """无 AssistNowToken:冷启动即可,不得崩进程(comma 云端代理已删)。"""
  monkeypatch.setattr(pigeond, "Params", lambda: type("P", (), {"get": lambda self, k: None})())
  with pytest.raises(RuntimeError):
    pigeond.get_assistnow_messages()
