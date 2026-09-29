"""07 号：「远程大模型」设置面板——开关（仅 offroad 生效）+ 服务器地址（空 = 自动发现）。

C4 侧不做超时率等统计（用户拍板：数据只在手机侧看）。
"""
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigToggle
from openpilot.selfdrive.ui.mici.widgets.dialog import BigInputDialog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller

HOST_AUTO_TEXT = "自动发现"


def _host_display(host: str) -> str:
  return host if host else HOST_AUTO_TEXT


class BigmodelLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()

    self._toggle = BigToggle(text="远程大模型",
                             initial_state=bool(ui_state.params.get("BigmodelToggle", return_default=True)),
                             toggle_callback=self._toggle_callback)
    self._toggle.set_enabled(lambda: ui_state.is_offroad())  # 仅 offroad 生效

    self._host_btn = BigButton("服务器地址", _host_display(ui_state.params.get("BigmodelServerHost") or ""))
    self._host_btn.set_click_callback(self._edit_host)

    self._scroller.add_widgets([
      self._toggle,
      self._host_btn,
    ])

  def _update_state(self):
    super()._update_state()
    self._toggle.set_checked(bool(ui_state.params.get("BigmodelToggle", return_default=True)))

  @staticmethod
  def _toggle_callback(state: bool):
    ui_state.params.put_bool("BigmodelToggle", state)
    ui_state.update_params()

  def _edit_host(self):
    current = ui_state.params.get("BigmodelServerHost") or ""
    dlg = BigInputDialog("服务器地址（留空=自动发现）", current, minimum_length=0, confirm_callback=self._set_host)
    gui_app.push_widget(dlg)

  def _set_host(self, host: str):
    host = host.strip()
    if host:
      ui_state.params.put("BigmodelServerHost", host)
    else:
      ui_state.params.remove("BigmodelServerHost")
    self._host_btn.set_value(_host_display(host))
