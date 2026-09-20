"""BLE GATT client transport over BlueZ / D-Bus (jeepney), following the BR/EDR
precedent in openpilot/sunnypilot/system/bluetooth/bluez.py.

Protocol per spec §7: command characteristic (write-with-response, 9-byte frame
[op u8][seq u8][button u8][mode u8][count u8][duration_ms u16][interval_ms u16]
little-endian — the spec's "8 字节" label miscounts its own field list), status
characteristic (read + notify, 4-byte frame [seq][state][pending][fw]).

真机行为(配对、连接参数、notify 时序)为上车验证项 6(spec §11):固件照 spec §7
实现,本模块只负责 central 侧的 GATT client 调用序列,单测以 FakeRouter 验证。
"""

from queue import Empty

from jeepney import DBusAddress, MatchRule, new_method_call
from jeepney.io.threading import DBusRouter, open_dbus_connection
from jeepney.low_level import HeaderFields, MessageType

from openpilot.sunnypilot.system.bluetooth.bluez import unwrap_variant


BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
DEVICE_IFACE = "org.bluez.Device1"
GATT_CHARACTERISTIC_IFACE = "org.bluez.GattCharacteristic1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


def _default_dbus_factory():
  return DBusRouter(open_dbus_connection(bus="SYSTEM"))


class GattTransport:
  """GATT client for the cruise-button simulator (spec §7).

  connect()    resolve device by address -> org.bluez.Device1.Connect ->
               resolve command/status characteristic paths
  notify()     subscribe PropertiesChanged on the status characteristic
               (match rule first, then StartNotify) so no frame is missed
  write(data)  WriteValue on the command characteristic (write-with-response)
  drain()      queued status frames from PropertiesChanged notifications
  notify_stop() / disconnect()  tear down subscription and connection
  """

  def __init__(self, service_uuid: str, command_uuid: str, status_uuid: str, address: str, dbus_factory=None):
    self.service_uuid = service_uuid.lower()
    self.command_uuid = command_uuid.lower()
    self.status_uuid = status_uuid.lower()
    self.address = address
    self._dbus_factory = dbus_factory or _default_dbus_factory
    self._router = None
    self._notify_filter = None
    self._device_path: str | None = None
    self.command_path: str | None = None
    self.status_path: str | None = None
    self._connected = False

  @property
  def connected(self) -> bool:
    return self._connected

  def connect(self) -> None:
    router = self._dbus_factory()
    try:
      objects = self._managed_objects(router)
      device_path = self._find_device_path(objects)
      self._call(router, device_path, DEVICE_IFACE, "Connect", timeout=30.0)
      objects = self._managed_objects(router)
      self.command_path = self._find_characteristic_path(objects, self.command_uuid)
      self.status_path = self._find_characteristic_path(objects, self.status_uuid)
      self._device_path = device_path
    except Exception:
      self._connected = False
      try:
        router.close()
      except Exception:
        pass
      raise
    self._router = router
    self._connected = True

  def notify(self) -> None:
    if self._router is None or self.status_path is None:
      raise RuntimeError("GATT transport is not connected")
    rule = MatchRule(interface=PROPERTIES_IFACE, member="PropertiesChanged", path=self.status_path)
    self._notify_filter = self._router.filter(rule, bufsize=20)
    self._call(self._router, self.status_path, GATT_CHARACTERISTIC_IFACE, "StartNotify")

  def write(self, data: bytes) -> None:
    if self._router is None or self.command_path is None:
      raise RuntimeError("GATT transport is not connected")
    self._call(self._router, self.command_path, GATT_CHARACTERISTIC_IFACE, "WriteValue",
               signature="aya{sv}", body=(bytes(data), {}))

  def drain(self) -> list[bytes]:
    if self._notify_filter is None:
      return []
    frames: list[bytes] = []
    queue = self._notify_filter.queue
    while True:
      try:
        message = queue.get_nowait()
      except Empty:
        break
      frames.extend(self._frames_from_message(message))
    return frames

  def notify_stop(self) -> None:
    if self._notify_filter is None:
      return
    if self._router is not None and self.status_path is not None:
      try:
        self._call(self._router, self.status_path, GATT_CHARACTERISTIC_IFACE, "StopNotify")
      except Exception:
        pass
    self._notify_filter.close()
    self._notify_filter = None

  def disconnect(self) -> None:
    self.notify_stop()
    router, self._router = self._router, None
    self._connected = False
    if router is None:
      return
    try:
      if self._device_path is not None:
        self._call(router, self._device_path, DEVICE_IFACE, "Disconnect", timeout=15.0)
    except Exception:
      pass
    try:
      router.close()
    except Exception:
      pass

  def _call(self, router, path: str, interface: str, member: str,
            signature: str | None = None, body: tuple = (), timeout: float = 15.0):
    address = DBusAddress(path, bus_name=BLUEZ, interface=interface)
    message = new_method_call(address, member, signature, body) if signature is not None else new_method_call(address, member)
    reply = router.send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type == MessageType.error:
      error_name = reply.header.fields.get(HeaderFields.error_name, "org.bluez.Error.Failed")
      detail = reply.body[0] if reply.body else error_name
      raise RuntimeError(str(detail))
    return reply.body

  def _managed_objects(self, router) -> dict:
    body = self._call(router, "/", OBJECT_MANAGER, "GetManagedObjects", timeout=15.0)
    return unwrap_variant(body[0]) if body else {}

  def _find_device_path(self, objects: dict) -> str:
    target = self.address.upper()
    for path, interfaces in objects.items():
      props = interfaces.get(DEVICE_IFACE)
      if props and str(unwrap_variant(props.get("Address", ""))).upper() == target:
        return path
    raise RuntimeError(f"Bluetooth device {self.address} was not found")

  def _find_characteristic_path(self, objects: dict, uuid: str) -> str:
    for path, interfaces in objects.items():
      props = interfaces.get(GATT_CHARACTERISTIC_IFACE)
      if props and str(unwrap_variant(props.get("UUID", ""))).lower() == uuid:
        return path
    raise RuntimeError(f"GATT characteristic {uuid} was not found")

  @staticmethod
  def _frames_from_message(message) -> list[bytes]:
    try:
      _interface, changed, _invalidated = message.body
    except (TypeError, ValueError):
      return []
    value = changed.get("Value") if isinstance(changed, dict) else None
    if value is None:
      return []
    return [bytes(unwrap_variant(value))]
