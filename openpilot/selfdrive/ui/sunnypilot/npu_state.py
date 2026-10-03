"""07 号：NPU 图标颜色规则（纯函数，无 UI 依赖）。

二色（2026-09-30 用户拍板，取代旧三态/滞回/提示横幅）：
  - 「远程大模型」关：不显示（调用方判断）。
  - 开 + onroad + 已连上（BigmodelLinkState ∈ connected/blip/restart）：绿。
  - 开 + 其余一切（offroad、connecting、lost、""）：橙。
BigmodelLinkState 是 CLEAR_ON_MANAGER_START、offroad 保留上趟旧值，故必须按 started 门控；
bigmodeld 进程启动时写一次 connecting，防新一趟起步读到上趟 connected 闪绿。
"""

LINK_CONNECTED = ("connected", "blip", "restart")  # 06 号链路状态串的「已连上」取值


def npu_color(started: bool, link_state: str) -> str:
  """"green" iff onroad 且链路已连上，其余一切 "orange"。"""
  return "green" if started and link_state in LINK_CONNECTED else "orange"
