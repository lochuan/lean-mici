import logging
import os
import time
from logging.handlers import BaseRotatingHandler
from pathlib import Path

import zmq

from openpilot.common.logging_extra import SwagLogger, SwagFormatter, SwagLogFileFormatter
from openpilot.common.hardware.hw import Paths


def get_file_handler():
  Path(Paths.swaglog_root()).mkdir(parents=True, exist_ok=True)
  base_filename = os.path.join(Paths.swaglog_root(), "swaglog")
  handler = SwaglogRotatingFileHandler(base_filename)
  return handler

class SwaglogRotatingFileHandler(BaseRotatingHandler):
  def __init__(self, base_filename, interval=60, max_bytes=1024*256, backup_count=2500, encoding=None):
    super().__init__(base_filename, mode="a", encoding=encoding, delay=True)
    self.base_filename = base_filename
    self.interval = interval # seconds
    self.max_bytes = max_bytes
    self.backup_count = backup_count
    self.log_files = self.get_existing_logfiles()
    log_indexes = [f.split(".")[-1] for f in self.log_files]
    self.last_file_idx = max([int(i) for i in log_indexes if i.isdigit()] or [-1])
    self.last_rollover = 0.0
    self.doRollover()

  def _open(self):
    self.last_rollover = time.monotonic()
    self.last_file_idx += 1
    next_filename = f"{self.base_filename}.{self.last_file_idx:010}"
    stream = open(next_filename, self.mode, encoding=self.encoding)
    self.log_files.insert(0, next_filename)
    return stream

  def get_existing_logfiles(self):
    log_files = []
    base_dir = os.path.dirname(self.base_filename)
    for fn in os.listdir(base_dir):
      fp = os.path.join(base_dir, fn)
      if fp.startswith(self.base_filename) and os.path.isfile(fp):
        log_files.append(fp)
    # newest first, matching _open()'s insert(0, ...) so doRollover()'s pop() deletes the oldest
    return sorted(log_files, reverse=True)

  def shouldRollover(self, record):
    size_exceeded = self.max_bytes > 0 and self.stream.tell() >= self.max_bytes
    time_exceeded = self.interval > 0 and self.last_rollover + self.interval <= time.monotonic()
    return size_exceeded or time_exceeded

  def doRollover(self):
    if self.stream:
      self.stream.close()
    self.stream = self._open()

    if self.backup_count > 0:
      while len(self.log_files) > self.backup_count:
        to_delete = self.log_files.pop()
        if os.path.exists(to_delete): # just being safe, should always exist
          os.remove(to_delete)

class UnixDomainSocketHandler(logging.Handler):
  def __init__(self, formatter):
    logging.Handler.__init__(self)
    self.setFormatter(formatter)
    self.pid = None

    self.zctx = None
    self.sock = None

  def __del__(self):
    self.close()

  def close(self):
    if self.sock is not None:
      self.sock.close()
    if self.zctx is not None:
      self.zctx.term()
    # 重置状态：close 后必须能通过 emit 里的 pid/closed 检查重新 connect，
    # 否则 closed socket 会以 ENOTSOCK 永久毒死所有后续日志调用（曾打死
    # lanlinkd 的 wifi connect worker：首连必挂、已保存网络才幸免）
    self.sock = None
    self.zctx = None
    self.pid = None

  def connect(self):
    self.zctx = zmq.Context()
    self.sock = self.zctx.socket(zmq.PUSH)
    self.sock.setsockopt(zmq.LINGER, 10)
    self.sock.connect(Paths.swaglog_ipc())
    self.pid = os.getpid()

  def emit(self, record):
    try:
      # fork 检测 + 坏 socket 自愈：close()/term() 之后 sock.closed 置真，
      # 必须 reconnect，否则 send 抛 ENOTSOCK 直接打死调用方
      if os.getpid() != self.pid or self.sock is None or self.sock.closed:
        self.connect()

      msg = self.format(record).rstrip('\n')
      try:
        s = chr(record.levelno)+msg
        self.sock.send(s.encode('utf8'), zmq.NOBLOCK)
      except zmq.error.Again:
        # drop :/
        pass
    except Exception:
      # 日志永远不能打断业务调用：宁可丢这条，也重建 socket 保住后续日志
      try:
        self.connect()
      except Exception:
        pass


class ForwardingHandler(logging.Handler):
  def __init__(self, target_logger):
    super().__init__()
    self.target_logger = target_logger

  def emit(self, record):
    self.target_logger.handle(record)


def add_file_handler(log):
  """
  Function to add the file log handler to swaglog.
  This can be used to store logs when logmessaged is not running.
  """
  handler = get_file_handler()
  handler.setFormatter(SwagLogFileFormatter(log))
  log.addHandler(handler)


cloudlog = log = SwagLogger()
log.setLevel(logging.DEBUG)


outhandler = logging.StreamHandler()

print_level = os.environ.get('LOGPRINT', 'warning')
if print_level == 'debug':
  outhandler.setLevel(logging.DEBUG)
elif print_level == 'info':
  outhandler.setLevel(logging.INFO)
elif print_level == 'warning':
  outhandler.setLevel(logging.WARNING)

ipchandler = UnixDomainSocketHandler(SwagFormatter(log))

log.addHandler(outhandler)
# logs are sent through IPC before writing to disk to prevent disk I/O blocking
log.addHandler(ipchandler)
