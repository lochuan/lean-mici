#!/usr/bin/env python3
"""只部署 LANLink 前端到设备，不做 OTA。

前端是纯静态文件（openpilot/system/lanlinkd/static/），改版面不涉及任何
Python/原生代码，所以没必要走 release + reset 那一整套流程——构建完直接把
static/ 推上去、重启 lanlinkd 就行，几秒钟的事。

做了三件容易出错的事：
  1. 同步前**校验产物是否最新**（tools/lanlink/build_lanlink_web.py --check），
     否则会把旧界面推上去且毫无提示。
  2. 清理设备上**已失效的 hash 文件**。Vite 的文件名带内容 hash，每次构建
     换名字，只做 scp 会让旧 JS/CSS 一直堆在 assets/ 里（实测堆了 3 份）。
     它们还带着 immutable 长缓存，磁盘和困惑都是白给的。
  3. 剔除 macOS 的 ._* AppleDouble 文件——tar 从 mac 打包时会带上它们，
     scp 到设备后会被当成静态资源。

用法：
  tools/lanlink/deploy_lanlink_web.py                     # 构建 + 部署 + 重启
  tools/lanlink/deploy_lanlink_web.py --no-build          # 只部署当前产物
  tools/lanlink/deploy_lanlink_web.py --host comma@1.2.3.4
  tools/lanlink/deploy_lanlink_web.py --set-password PW   # 顺便设置访问密码
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "openpilot/system/lanlinkd/static"
DEFAULT_HOST = "comma@10.223.134.33"
REMOTE_ROOT = "/data/openpilot"
REMOTE_STATIC = f"{REMOTE_ROOT}/openpilot/system/lanlinkd/static"

# 设备上的 python 与依赖（sanic 等随 AGNOS venv，19.7.2 起不再需要 /data/pydeps）
REMOTE_PY = "/usr/local/venv/bin/python"
REMOTE_PYPATH = f"{REMOTE_ROOT}"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
  return subprocess.run(cmd, check=True, **kw)


def ssh(host: str, script: str, capture: bool = False) -> subprocess.CompletedProcess:
  """在设备上跑一段 shell。用 -T 避免伪终端干扰输出。"""
  return subprocess.run(
    ["ssh", "-T", "-o", "ConnectTimeout=15", host, script],
    check=True, text=True,
    capture_output=capture,
  )


def referenced_assets() -> set[str]:
  """index.html 真正引用的 assets 文件名。"""
  html = (STATIC_DIR / "index.html").read_text()
  return set(re.findall(r"/assets/([A-Za-z0-9._\-]+)", html))


def build() -> None:
  print("==> 构建前端")
  run(["python3", str(REPO_ROOT / "tools/lanlink/build_lanlink_web.py"), "--build"])


def check_fresh() -> None:
  print("==> 校验产物与源码一致")
  r = subprocess.run(
    ["python3", str(REPO_ROOT / "tools/lanlink/build_lanlink_web.py"), "--check"],
    text=True, capture_output=True,
  )
  sys.stdout.write(r.stdout)
  if r.returncode != 0:
    sys.stderr.write(r.stderr)
    raise SystemExit("产物已过期，请先运行 tools/lanlink/deploy_lanlink_web.py（不加 --no-build）")


def make_tarball(dest: Path) -> None:
  """打包 static/，跳过 macOS 的 ._* 垃圾文件。"""
  def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    name = Path(info.name).name
    if name.startswith("._") or name == ".DS_Store":
      return None
    return info

  with tarfile.open(dest, "w:gz") as tar:
    tar.add(STATIC_DIR, arcname="static", filter=keep)


def deploy(host: str) -> None:
  keep = referenced_assets()
  if not keep:
    raise SystemExit("index.html 里没有引用任何 /assets/ 文件，产物可能损坏")
  print(f"==> 部署到 {host}（引用资源 {len(keep)} 个）")

  with tempfile.TemporaryDirectory() as tmp:
    tarball = Path(tmp) / "lanlink_static.tgz"
    make_tarball(tarball)
    size_kb = tarball.stat().st_size / 1024
    print(f"    打包 {size_kb:.0f} KB")
    run(["scp", "-q", str(tarball), f"{host}:/tmp/lanlink_static.tgz"])

  # 整目录替换：先删再解，避免旧 hash 文件残留。
  # keep 列表只作为解包后的完整性校验。
  keep_list = " ".join(shlex.quote(k) for k in sorted(keep))
  script = f"""
set -e
cd {shlex.quote(REMOTE_STATIC)}/..
rm -rf static
tar xzf /tmp/lanlink_static.tgz
find static -name '._*' -delete 2>/dev/null || true
rm -f /tmp/lanlink_static.tgz
missing=""
for f in {keep_list}; do
  [ -f "static/assets/$f" ] || missing="$missing $f"
done
if [ -n "$missing" ]; then
  echo "解包后缺少资源:$missing" >&2
  exit 1
fi
echo "    设备上的文件:"
find static -type f | sort | sed 's/^/      /'
"""
  ssh(host, script)


def set_password(host: str, password: str) -> None:
  """直接写 LanLinkPasswordHash。

  走 auth.hash_password 而不是 HTTP /api/setup：后者在已设密码时返回 409，
  而这里的语义是"设成这个"（含改密）。哈希在**设备上**计算，密码不会出现在
  scp 的文件里，只作为参数传给设备上的 python。
  """
  print("==> 设置访问密码")
  remote = (
    "from openpilot.common.params import Params;"
    "from openpilot.system.lanlinkd.auth import hash_password, MIN_PASSWORD_LEN;"
    "import sys;"
    "pw = sys.argv[1];"
    "assert len(pw) >= MIN_PASSWORD_LEN, f'密码至少 {MIN_PASSWORD_LEN} 位';"
    "Params().put('LanLinkPasswordHash', hash_password(pw), block=True);"
    "print('    密码已更新（旧 token 会在 lanlinkd 重启后失效）')"
  )
  script = (
    f"cd {shlex.quote(REMOTE_ROOT)} && "
    f"PYTHONPATH={shlex.quote(REMOTE_PYPATH)} {shlex.quote(REMOTE_PY)} "
    f"-c {shlex.quote(remote)} {shlex.quote(password)}"
  )
  ssh(host, script)


def restart(host: str) -> None:
  """重启 lanlinkd。

  它由 manager 托管（LanLinkEnabled 控制），杀掉进程即可让 manager 重新拉起；
  没被托管时（比如手动起的预览进程）就只是停掉。

  这里绕开一个陷阱：任何按名字匹配的写法（pkill -f / pgrep -f）都会命中
  **承载这条命令的 ssh shell 自己**，因为模式串就在它的命令行里。之前因此
  自杀并返回 255，改用 $$ 排除也不行——真正执行的是 `bash -c` 那个子进程，
  pid 与 $$ 不同。

  所以改成按**可执行文件**判定：只杀 cmdline 里真正跑 python 的那些进程，
  bash/ssh 一律跳过。
  """
  print("==> 重启 lanlinkd")
  script = r"""
set +e
killed=0
for p in $(pgrep -f 'openpilot\.system\.lanlinkd' 2>/dev/null); do
  cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null) || continue
  # 只认 python 进程；承载本命令的 bash/ssh 会匹配到模式串但不是目标
  case "$cmd" in
    *python*) ;;
    *) continue ;;
  esac
  if kill "$p" 2>/dev/null; then
    echo "    已停止 pid $p"
    killed=$((killed + 1))
  fi
done
[ "$killed" -eq 0 ] && echo "    没有正在运行的 lanlinkd 进程"
sleep 2
running=""
for p in $(pgrep -f 'openpilot\.system\.lanlinkd' 2>/dev/null); do
  cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null) || continue
  case "$cmd" in
    *python*) running="$running  $p" ;;
  esac
done
if [ -n "$running" ]; then
  echo "    manager 已重新拉起:$running"
else
  echo "    等待 manager 按 LanLinkEnabled 拉起"
fi
exit 0
"""
  ssh(host, script)


def main() -> int:
  ap = argparse.ArgumentParser(
    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--host", default=DEFAULT_HOST, help=f"设备 ssh 地址（默认 {DEFAULT_HOST}）")
  ap.add_argument("--no-build", action="store_true", help="跳过构建，只部署现有产物")
  ap.add_argument("--no-restart", action="store_true", help="部署后不重启 lanlinkd")
  ap.add_argument("--set-password", metavar="PW", help="同时设置 LANLink 访问密码")
  args = ap.parse_args()

  if args.no_build:
    check_fresh()
  else:
    build()

  deploy(args.host)

  if args.set_password:
    set_password(args.host, args.set_password)

  if not args.no_restart:
    restart(args.host)

  print("\n完成。前端是静态资源，无需 OTA；刷新浏览器即可看到新界面。")
  print("（index.html 是 no-cache，assets 带内容 hash，所以不会拿到旧缓存）")
  return 0


if __name__ == "__main__":
  try:
    sys.exit(main())
  except subprocess.CalledProcessError as e:
    sys.exit(f"命令失败（exit {e.returncode}）：{' '.join(map(str, e.cmd))}")
