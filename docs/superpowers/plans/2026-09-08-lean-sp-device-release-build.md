# lean-sp 设备端 release 构建脚本 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 设备端一键构建 lean-sp release 分支：worktree 隔离 + release_files 裁剪 + AGNOS venv 串行 scons + RELEASE panda + test_onroad 门禁 + 推 release-mici 分支 + 激活 /data/openpilot + 重启冒烟。

**Architecture:** 单一 bash 脚本 `tools/release/build_lean_release.sh`（设备端跑），镜像官方 `build_release.sh` 的 worktree→裁剪→scons→panda→校验→test_onroad→prebuilt→push 流程，但用 AGNOS 内置 venv（`/usr/local/venv`）替代 uv venv（避免 /home 重启清空），-j1 串行（设备 4 核），opendbc 用 lochuan toyota-only 裁剪版。

**Tech Stack:** bash, git worktree, AGNOS /usr/local/venv (Python 3.12.3, 内置 SCons/acados/capnproto/eigen/ffmpeg/json11/ncurses/zeromq/zstd), scons, RELEASE=1 scons panda/, test_onroad.py

**Spec:** 见对话——用户确认：设备端构建 + 推 release 分支 + test_onroad 门禁 + AGNOS venv（解决 /home 清空）。

## Global Constraints
- 设备：comma@10.205.161.33，AGNOS 19.7，4 核 ARM64，/ 4.3G(90%+)，/home 100M overlay（重启清空）
- 构建用 AGNOS 内置 venv `/usr/local/venv`（不依赖 /home 的 uv venv，重启安全）
- scons 串行 `-j1`（设备 4 核，并行 rednose generator OOM）
- opendbc 用 lochuan toyota-only（d254ad5c），不跟上游
- push 用 `GIT_LFS_SKIP_PUSH=1`（macOS→GitHub LFS 被 Cloudflare 拦）
- manager.py/build.py shebang 已是 `/usr/local/venv/bin/python`（commit 3cc89de81）
- RELEASE=1 必须（test_onroad 是 release 门禁）

---

### Task 1: 写 build_lean_release.sh 脚本

**Files:**
- Create: `tools/release/build_lean_release.sh`

**Interfaces:**
- Consumes: `tools/release/release_files.py`（裁剪清单）、`tools/release/identity.sh`（git 身份）、`test_onroad.py`（门禁）、AGNOS venv
- Produces: `release-mici` 分支（fork 远程）+ /data/openpilot 激活为 release 构建

**Steps:**

1. 写脚本（10 段，见下方完整内容），`chmod +x`
2. `bash -n` 语法检查
3. 推到设备 /data/openpilot/tools/release/
4. 设备端跑 worktree+裁剪+scons+panda 段（Task 2 验证）
5. 设备端跑 test_onroad 段（Task 3 验证）
6. 推 release-mici + 激活 + 重启（Task 4 验证）

---

### Task 2: 设备端 worktree+裁剪+scons+panda 构建

设备端跑脚本前 5 段，确认 scons done + panda RELEASE build + 无 submodule。

### Task 3: 设备端 test_onroad 门禁

`RELEASE=1 ./openpilot/selfdrive/test/test_onroad.py`，25s onroad 拉起 + CPU/timing 检查全绿。

### Task 4: 推 release-mici 分支 + 激活 + 重启冒烟

git push release-mici → /data/openpilot 切到 release-mici → touch prebuilt → systemctl restart comma → offroad 进程组全绿。
