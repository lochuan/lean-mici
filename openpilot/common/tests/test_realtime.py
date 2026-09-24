"""config_best_effort_process 的全进程线程钉核。

2026-09-25 设备实测抓到的漏洞:eagled 的 numpy/OpenBLAS 线程池在 import 期
就带着全核掩码(0-7,含 core 1)存在,set_core_affinity 只钉主线程——
"core 1 不可触碰"的设计红线被这些线程穿透。修复:遍历 /proc/self/task
把进程内**所有**线程钉到安全核;后续新建线程继承被钉后的创建者掩码。
"""
from openpilot.common import realtime


def test_pin_all_threads_constrains_every_tid(monkeypatch, tmp_path):
  task = tmp_path / "task"
  task.mkdir()
  (task / "10").mkdir()
  (task / "22").mkdir()
  pinned = []
  monkeypatch.setattr(realtime.os, "sched_setaffinity",
                      lambda tid, cores: pinned.append((tid, list(cores))), raising=False)
  realtime._pin_all_threads([0, 2, 3], task_dir=str(task))
  assert sorted(pinned) == [(10, [0, 2, 3]), (22, [0, 2, 3])]


def test_pin_all_threads_tolerates_threads_that_exited(monkeypatch, tmp_path):
  task = tmp_path / "task"
  task.mkdir()
  (task / "10").mkdir()
  (task / "22").mkdir()
  pinned = []

  def fake(tid, cores):
    if tid == 10:
      raise OSError("thread exited between listdir and pin")
    pinned.append(tid)

  monkeypatch.setattr(realtime.os, "sched_setaffinity", fake, raising=False)
  realtime._pin_all_threads([0, 2, 3], task_dir=str(task))
  assert pinned == [22]


def test_pin_all_threads_noop_without_proc(tmp_path):
  """开发机(macOS)无 /proc/self/task:安静跳过,不抛异常。"""
  realtime._pin_all_threads([0], task_dir=str(tmp_path / "nonexistent"))
