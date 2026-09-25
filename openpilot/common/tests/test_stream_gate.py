"""观测流接收门测试：表驱动钉死「新鲜」的唯一定义（fix/stream-gate）。

判定语义只有这一份：FRESH 要求收到（age 非 None）+ envelope valid + 年龄在
阈值内（边界 age == 阈值仍算新鲜，与 lanes/avoidanced 的历史边界一致）。
回退动作是消费方语义，不在这层。
"""
import pytest

from openpilot.common.stream_gate import MAX_AGE_S, StreamStatus, stream_status


def test_policy_table_covers_all_gated_streams():
  # 四条被门控的观测流，阈值 1s 量级，一处定义
  assert MAX_AGE_S == {"eagleState": 1.0, "lateralManeuverPlan": 1.0,
                       "eagleDebug": 1.0, "modelV2": 1.0}


def test_unknown_stream_is_programming_error():
  with pytest.raises(KeyError):
    stream_status("radarState", 0.1)


@pytest.mark.parametrize("valid", [True, False])
def test_never_received_is_never_seen_regardless_of_valid(valid):
  assert stream_status("eagleState", None, valid=valid) is StreamStatus.NEVER_SEEN


@pytest.mark.parametrize("stream", list(MAX_AGE_S))
def test_fresh_at_zero_age(stream):
  assert stream_status(stream, 0.0) is StreamStatus.FRESH


@pytest.mark.parametrize("stream", list(MAX_AGE_S))
def test_fresh_at_exact_threshold(stream):
  # 边界语义：age == 阈值仍新鲜，只有严格超过才 STALE
  assert stream_status(stream, MAX_AGE_S[stream]) is StreamStatus.FRESH


@pytest.mark.parametrize("stream", list(MAX_AGE_S))
def test_stale_beyond_threshold(stream):
  assert stream_status(stream, MAX_AGE_S[stream] + 1e-6) is StreamStatus.STALE


def test_invalid_envelope_is_stale_even_at_zero_age():
  assert stream_status("eagleDebug", 0.0, valid=False) is StreamStatus.STALE
