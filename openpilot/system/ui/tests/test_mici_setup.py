import urllib.error

from openpilot.cereal import log
from openpilot.system.ui import mici_setup
from openpilot.system.ui.mici_setup import NetworkConnectivityMonitor

NetworkType = log.DeviceState.NetworkType


def block_internet_requests(*args, **kwargs):
  raise urllib.error.URLError("internet unreachable")


def run_monitor_briefly(monkeypatch, network_type) -> NetworkConnectivityMonitor:
  monkeypatch.setattr(mici_setup.HARDWARE, "get_network_type", lambda: network_type)
  monkeypatch.setattr(mici_setup.urllib.request, "urlopen", block_internet_requests)
  monitor = NetworkConnectivityMonitor()
  monitor.start()
  monitor.network_connected.wait(timeout=1.5)
  monitor.stop()
  return monitor


# 国产安卓热点上联网探测请求会长时间失败（iPhone 热点正常，原因未查明），所以有默认路由就放行
def test_default_route_counts_as_connected_even_when_internet_unreachable(monkeypatch):
  monitor = run_monitor_briefly(monkeypatch, NetworkType.wifi)
  assert monitor.network_connected.is_set()
  assert monitor.wifi_connected.is_set()


def test_no_default_route_is_not_connected(monkeypatch):
  monitor = run_monitor_briefly(monkeypatch, NetworkType.none)
  assert not monitor.network_connected.is_set()
  assert not monitor.wifi_connected.is_set()


def test_monitor_survives_network_type_error(monkeypatch):
  network_type_results = iter([OSError("dbus hiccup")])

  def get_network_type():
    result = next(network_type_results, NetworkType.wifi)
    if isinstance(result, Exception):
      raise result
    return result

  monkeypatch.setattr(mici_setup.HARDWARE, "get_network_type", get_network_type)
  monitor = NetworkConnectivityMonitor()
  monitor.start()
  try:
    assert monitor.network_connected.wait(timeout=3)
  finally:
    monitor.stop()
