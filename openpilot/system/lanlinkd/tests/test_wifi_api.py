"""wifi_api 纯逻辑单测：payload 校验与静态 ipv4 字段规范化。

对齐 test_bluetooth_api.py 的风格：不碰 DBus，不碰 WifiManager。
"""
import pytest

from openpilot.system.lanlinkd import wifi_api


class TestValidateStaticConfig:
  def test_valid_minimal(self):
    cfg = wifi_api.validate_static_config({
      "ip": "192.168.1.50", "prefix": "24", "gateway": "192.168.1.1", "dns": ["1.1.1.1"],
    })
    assert cfg == {"ip": "192.168.1.50", "prefix": 24, "gateway": "192.168.1.1", "dns": ["1.1.1.1"]}

  def test_valid_max_dns(self):
    cfg = wifi_api.validate_static_config({
      "ip": "10.0.0.2", "prefix": 8, "gateway": "10.0.0.1", "dns": ["8.8.8.8", "1.1.1.1", "9.9.9.9"],
    })
    assert cfg["dns"] == ["8.8.8.8", "1.1.1.1", "9.9.9.9"]

  def test_dns_string_csv_split(self):
    cfg = wifi_api.validate_static_config({
      "ip": "10.0.0.2", "prefix": 24, "gateway": "10.0.0.1", "dns": "1.1.1.1, 8.8.8.8",
    })
    assert cfg["dns"] == ["1.1.1.1", "8.8.8.8"]

  @pytest.mark.parametrize("ip", ["999.1.1.1", "192.168.1", "abc", ""])
  def test_bad_ip(self, ip):
    with pytest.raises(wifi_api.WifiValidationError):
      wifi_api.validate_static_config({"ip": ip, "prefix": 24, "gateway": "192.168.1.1", "dns": ["1.1.1.1"]})

  @pytest.mark.parametrize("gateway", ["999.1.1.1", "not-an-ip", ""])
  def test_bad_gateway(self, gateway):
    with pytest.raises(wifi_api.WifiValidationError):
      wifi_api.validate_static_config({"ip": "192.168.1.50", "prefix": 24, "gateway": gateway, "dns": ["1.1.1.1"]})

  @pytest.mark.parametrize("prefix", [-1, 33, "abc", None])
  def test_bad_prefix(self, prefix):
    with pytest.raises(wifi_api.WifiValidationError):
      wifi_api.validate_static_config({"ip": "192.168.1.50", "prefix": prefix, "gateway": "192.168.1.1", "dns": ["1.1.1.1"]})

  def test_empty_dns_rejected(self):
    # method=manual 下没有 DHCP 兜底，空 DNS 会静默断网
    with pytest.raises(wifi_api.WifiValidationError):
      wifi_api.validate_static_config({"ip": "192.168.1.50", "prefix": 24, "gateway": "192.168.1.1", "dns": []})
    with pytest.raises(wifi_api.WifiValidationError):
      wifi_api.validate_static_config({"ip": "192.168.1.50", "prefix": 24, "gateway": "192.168.1.1"})

  def test_too_many_dns(self):
    with pytest.raises(wifi_api.WifiValidationError):
      wifi_api.validate_static_config({
        "ip": "192.168.1.50", "prefix": 24, "gateway": "192.168.1.1",
        "dns": ["1.1.1.1", "8.8.8.8", "9.9.9.9", "7.7.7.7"],
      })


class TestValidateConnectBody:
  def test_auto_minimal(self):
    code, msg, payload = wifi_api.validate_connect_body({"ssid": "home", "password": ""})
    assert code == 0
    assert payload == {"ssid": "home", "password": "", "hidden": False}

  def test_static_carried_through(self):
    code, _, payload = wifi_api.validate_connect_body({
      "ssid": "home", "password": "supersecret1",
      "static": {"ip": "192.168.1.50", "prefix": 24, "gateway": "192.168.1.1", "dns": ["1.1.1.1"]},
    })
    assert code == 0
    assert payload["static"]["gateway"] == "192.168.1.1"

  def test_bad_static_fails_payload(self):
    code, msg, _ = wifi_api.validate_connect_body({
      "ssid": "home", "password": "supersecret1", "static": {"ip": "1.2.3", "gateway": "", "dns": []},
    })
    assert code == 400

  def test_missing_ssid(self):
    code, _, _ = wifi_api.validate_connect_body({"password": "x"})
    assert code == 400

  def test_short_psk(self):
    code, msg, _ = wifi_api.validate_connect_body({"ssid": "home", "password": "short"})
    assert code == 400
