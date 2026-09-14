# system/lanlinkd/software_api.py
"""软件页纯逻辑（lanlink 的 /api/software/*），对应 mici software.py 的功能子集。

后端职责：
  - 从 params 组装状态快照（version/branch/commit、UpdaterState、可更新描述）
  - check / install 两个动作：给 updated 进程发 SIGUSR1（检查）、
    置 DoReboot 参数（安装后重启生效）
  - offroad gating：check 与 install 均仅限停车（与 mici 一致：
    行车中 check 按钮也禁用）
"""

import re
import subprocess

UPDATED_PROC = "openpilot.system.updated.updated"
SIGNAL_CHECK = "-SIGUSR1"   # mici CheckUpdateButton.CHECK_FOR_UPDATE
SIGNAL_INSTALL = "-SIGHUP"  # DOWNLOAD_UPDATE -> 触发 fetch/download

_DO_REBOOT = "DoReboot"


def _split_description(desc: str):
  """UpdaterCurrent/NewDescription 格式: "version / branch / commit / date"。"""
  parts = [p.strip() for p in desc.split(" / ")]
  if len(parts) != 4:
    return None
  version, branch, commit, date = parts
  return {"version": version, "branch": branch, "commit": commit, "date": date}


def _pwd(params, key: str):
  from openpilot.system.lanlinkd.params_api import to_str
  return to_str(params.get(key)) or ""


def status(params) -> dict:
  cur_desc = _pwd(params, "UpdaterCurrentDescription")
  new_desc = _pwd(params, "UpdaterNewDescription")
  try:
    failed_count = int(_pwd(params, "UpdateFailedCount") or 0)
  except ValueError:
    failed_count = 0
  return {
    "version": _pwd(params, "Version"),
    "branch": _pwd(params, "GitBranch"),
    "commit": _pwd(params, "GitCommit"),
    "current": _split_description(cur_desc) if cur_desc else None,
    "updaterState": _pwd(params, "UpdaterState") or "idle",
    "updateAvailable": params.get_bool("UpdateAvailable"),
    "fetchAvailable": params.get_bool("UpdaterFetchAvailable"),
    "failedCount": failed_count,
    "failed": failed_count > 0,
    "newVersion": _split_description(new_desc) if new_desc else None,
    "targetBranch": _pwd(params, "UpdaterTargetBranch"),
    "availableBranches": [b for b in (_pwd(params, "UpdaterAvailableBranches") or "").split(",") if b],
    "offroad": params.get_bool("IsOffroad"),
  }


def signal(params, action: str) -> tuple[int, dict | str]:
  """check / install 动作。返回 (code, payload)；code==204 表示已受理。

  check: pkill -SIGUSR1 updated →下次循环 check & fetch
  install: 置 DoReboot → updated 自己 swap 后重启
  两个动作都只允许 offroad（与 mici 的按钮 disable 规则一致）。
  """
  if action not in ("check", "download", "install"):
    return 404, "Unknown software action."
  if not params.get_bool("IsOffroad"):
    return 409, "Software updates can only be installed offroad."
  if action == "install":
    # install = mici InstallUpdateButton：置 DoReboot，updated 完成渐变 swap
    # 后会 reboot 进入新 release。必须先有可装的更新。
    if not params.get_bool("UpdateAvailable"):
      return 409, "No update available."
    params.put_bool(_DO_REBOOT_KEY, True, block=True)
    return 200, {}

  sig = SIGNAL_CHECK
  proc = subprocess.run(
    ["pkill", sig, "-f", UPDATED_PROC],
    check=False, capture_output=True, text=True,
  )
  if proc.returncode != 0:
    # rc=1 且报错为空时是 pkill 没找到进程（updated 未运行）
    return 503, "updater process not running."
  return 200, {}

_DO_REBOOT_KEY = "DoReboot"


def sanitize_report_version(v: str) -> str:
  """供 UI 展示徽标用的版本号规范化（容错 2026.003.000 之类的奇怪输入）。"""
  return re.sub(r"[^0-9A-Za-z._-]", "", v)[:64]
