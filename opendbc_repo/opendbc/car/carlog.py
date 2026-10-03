import os
import logging

# set up logging
LOGPRINT = os.environ.get('LOGPRINT', 'INFO').upper()
carlog = logging.getLogger('carlog')
carlog.setLevel(LOGPRINT)
carlog.propagate = False

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(message)s'))
carlog.addHandler(handler)


# 已知无害的持续告警去重表(进程内)。SecOC 车型在 EPS bypass + 原车纵向的
# 形态下同步 MAC 每跳变都失配,逐帧刷 error 没有任何信息增量。
_reported_once: set = set()


def error_once(key: str, msg: str) -> None:
  """同 key 的 error 进程内只发一次(2026-09-25 用户裁定的已知告警降噪)。"""
  if key in _reported_once:
    return
  _reported_once.add(key)
  carlog.error(msg)
