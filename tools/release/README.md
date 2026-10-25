# 本地源车机发版

入口沿用 `publish_release_from_device.sh` 和 `device_release.sh`。当前只支持
`--local-source`，公开 lean-release 发布暂停；本地发布传输已批准的源码提交、固定
子模块及离线 Sanic 产物，不推公共远端。

## 离线 Sanic 产物

`uv.lock` 是唯一版本来源。构建器选择 Linux arm64 CPython 3.12 的固定 12 包：
aiofiles、html5tagger、httptools、multidict、sanic、sanic-routing、setuptools、tracerite、
typing-extensions、ujson、uvloop、websockets。校验 wheel SHA-256、METADATA 和依赖约束，
拒绝闭包变化或缺少目标 wheel；不包含 sanic-testing。

在固定 Linux arm64 构建环境、目标解释器和库中运行（路径指该环境）：

```bash
/usr/local/venv/bin/python tools/release/build_pydeps.py \
  /source "$APPROVED_SOURCE_SHA" --wheels /cache/wheels --output /output/sanic-artifact
```

默认离线；首次获取 wheel 可在构建端显式加 `--download`，随后禁网复跑。
构建使用 `pip --no-index --require-hashes --only-binary=:all: --no-compile --no-deps --target`；
车机不运行 pip、不联网补包。输出 `RELEASE_MANIFEST.json`、`<id>.tar.gz` 和衍生
`requirements.txt`。manifest 只记录源码 SHA、uv.lock 哈希，以及依赖 ID、归档哈希/
压缩字节数和完整 wheel 清单。ID 覆盖归档哈希与包清单，相同闭包可复用。

发布器校验源码 SHA、锁文件和归档后，把产物运到
`/data/build/.git/pydeps/<source-sha>/`；同源缓存必须逐字一致。prepare 在修改参数、
保存事务和停 comma 之前离线安装：持锁、安全解包到同文件系统暂存目录，使用目标
解释器核对全部 12 包的版本和导入来源，写 COMPLETE、fsync 后原子 rename 到
`/data/pydeps/sets/<id>/site-packages`。失败清理本次暂存，现有集合不覆盖；无 `current`
链接。完成文件 0444、目录 0555，发布后不应手动修改。stage 只附带 manifest 并核对
源码 SHA/uv.lock 哈希，保留已有 tinygrad pin 和单文件大小检查。

`launch_chffrplus.sh` 是唯一运行时 PYTHONPATH 入口：清除外来 PYTHONHOME/PYTHONPATH，
关闭 user site，按 manifest ID 和 COMPLETE 选择路径，应用路径在前。启动不再重复
全树哈希或导入探针。未安装/未完成时保留基础路径，通过既有 BLOCK 仅屏蔽 lanlinkd，
不改永久 LanLinkEnabled。安装时导入失败会在停服前拒绝发布。启动信任已安装集合，
不保证发现发布后的人为改动或存储损坏；解释器/应用 native 不兼容仍可能影响 manager/UI。

`AGNOS_VERSION` 仍为 19.6.29；boot 与后台更新均读取 `AGNOS_ACCEPTED_VERSIONS`，默认
接受 19.6.29 和 20.1，避免把 20.1 误判为更新目标；其他版本沿用原更新行为。
这只表达版本兼容，不代表已授权刷机或已通过真机验收。

回滚 checkout 后由旧应用自己的 manifest 选依赖，无跨 OS 回滚矩阵或自动 GC。
旧应用没有 pydeps 入口时仍依赖旧系统环境，不能把本流程当作跨系统恢复保证。
先单独获准在 19.6.29 上验证 12 包来源及断网重启后的 manager/UI/LANLink；刷写 20.1
属于后续独立任务。当前实现停在 ready-for-device-validation。

## 部署

仅在批准的驻车维护窗口、供电稳定且没有其他构建/部署任务时执行。命令会写设备、
停 comma、在 `/data/build` 强制 checkout/clean、使用 `/data/scons_cache` 构建，
剥成 `/data/relstage`，消费到 `/data/openpilot` 后整机重启；没有 dry-run。

```bash
LOCAL_SOURCE_REF=refs/heads/lean-master \
EXPECTED_SOURCE_SHA="${APPROVED_SOURCE_SHA:?先指定批准的源码提交}" \
PYDEPS_ARTIFACT=/tmp/sanic-artifact \
DEVICE=comma@10.0.0.27 \
bash tools/release/publish_release_from_device.sh --local-source --skip-smoke
```

`LOCAL_SOURCE_REF` 显式选择完整本地分支 ref，省略时仍为 `refs/heads/nav-test`。
仅接受 `refs/heads/` 下由英文字母、数字、下划线、点、斜杠、连字符组成且通过
Git ref 校验的名称；不接受 tag、SHA 或修订表达式。`EXPECTED_SOURCE_SHA` 必须是
批准的完整 40 位 SHA，并与该分支解析出的 commit 相同。HEAD、同名 tag 和未提交的
工作树修改不作为业务源码。发布分支仍固定为 `nav-local-release`。

prepare 要求当前 live HEAD 位于本地分支且跟踪树干净；记录当次旧 HEAD、完整分支
ref 和 DisableUpdates 原值，在 `/data/openpilot` 创建
`refs/local-release/rollback/<旧HEAD>` 保留旧对象。后续切换即使覆盖同名
`nav-local-release`，回滚仍恢复这次记录的旧 HEAD/分支。不会移动 `lean-release`，
不会清理历史回滚 ref；没有固定的回滚 SHA 或参数值。

源码传输在停服前；完整构建仍用原 SCons 流程。建议维护窗口 30–60 分钟，
冷构建/故障留 60–120 分钟；没有真机构建计时。

## DisableUpdates 和状态保留

部署先把 DisableUpdates 的原始字节或“不存在”持久保存到
`/data/build/.git/local-release-state.json`，再设为 `1`；回滚精确恢复原值，
原键不存在就删除。不备份 `/data/params`，也不回滚其他参数、设置或校准。

**回滚完成之前不能删除或重建 `/data/build`，也不能清掉它的 `.git`。**
状态文件和回滚模块 `/data/build/.git/local-release.py` 都在其中，构建时原有的
`git clean -ffdx` 会保留它们；`rm -rf /data/build` 不会。回滚完成后才可清理，
若还需保留发版审计记录，另行保存该状态文件。源码缓存只保留本次和上一代。

当前车机只读预检的原值为 `b'1'`；当次 prepare 会重新读取并持久记录实际原值，
不能沿用任何旧报告或上次状态的参数值。若状态文件或保留的回滚 ref 丢失，
回滚拒绝猜测。DisableUpdates 带 BACKUP 标志，
部署期间 sunnylink 的设置备份可能记下 `1`；后续恢复设置时要检查该键。

updated 只在启动时读 DisableUpdates；原构建停服后，新拉起的 updated 看到 `1`
会退出。停车期间 manager 可能约每秒两次重新拉起它，带来 CPU/进程创建和日志开销；
这是现有“禁用更新”设置的行为，未测量本车实际开销。行驶中 updated 不运行。
部署后只读观察停车时 CPU、updated PID 变化与日志；验收期间保持禁用更新，
避免不存在的远端 nav-local-release 分支报错或 OTA finalize 替换本地树。

## 轮询和失败处理

构建、消费、回滚都在设备端分离执行，不依赖 Mac SSH 持续连接。
消费先确认 live HEAD/分支仍与 prepare 一致，再核对 fetch SHA 和
`source commit: <记录的源分支>@<SHA>`，停服并 checkout，
核对 HEAD/跟踪树后，**先持久写 `rebooting` 再请求 reboot**。
Mac 看到该状态后等待重连并做启动、SHA 和参数检查。

再次部署时，旧 `rebooting` 只有在 boot ID 已变化、当前 HEAD/分支与旧 release
一致、提交来源与旧状态一致、DisableUpdates 仍为 `1`、comma/manager 正常且没有
reset 界面时，才能作为上次成功部署结束并重新记录本次基线。兼容旧版无 boot ID
的状态时，还要求状态文件 mtime 早于内核 `/proc/stat` 的 btime；时钟证据不能确认
时拒绝继续。旧状态的历史回滚 SHA/参数不作为新事务基线。
`prepared` 仅允许同源 SHA/分支、同 live HEAD/ref、同 boot ID 的重试；
`rolled-back` 必须与已恢复的 HEAD/ref/原参数一致且已经重启；其他状态不得开始新部署。

消费轮询为 72 次、间隔 5 秒，另有每次 SSH 连接耗时；“6 分钟”是轮询等待预算，
不是硬性墙钟截止、SCons 构建限时或设备任务限时。440 MB 级的 stage fetch 和
约 0.9 GB 的 checkout 能否在窗口内完成尚未实测。超时只是 Mac 未确认状态，
设备可能仍在消费并随后重启，Mac 会退出且不继续自动启动检查。
先查 `/tmp/consume.log` 和状态，不要另行重启或并发回滚。

本次事务中的消费/回滚失败会尝试恢复并验证 prepare 记录的旧 HEAD/分支、
恢复 DisableUpdates、start comma，
并输出回滚命令；失败路径不自动 reboot。分开处理状态：

- `recovered`：旧树及原参数已验证恢复；确认没有任务执行后，普通整机重启是安全的，
  可清除 tap-reset 的触摸计数。也可执行下述 rollback，由它校验后重启。
  若要重新部署，必须先 rollback；只 reboot 不会清掉 `recovered` 状态。
- `failed`、`switching` 或状态不明：先排查日志、任务和树，禁止盲目 reboot。
  持续磁盘/权限故障时只能保证尝试 start comma，不能保证业务恢复。
- prepare 之前失败：提示未进入参数设置/设备构建，不输出本次回滚命令。
  prepare 已发起但 SSH 失败时，仍提示查状态后回滚，因为原值可能已落盘或参数已写入。
  必须先确认 state 属于本次事务；错误源 SHA、陈旧 consume 请求或变动的 live 身份
  在修改代码/参数前拒绝，不得恢复其他事务的参数。

不创建 `/tmp/booted`；成功部署和回滚仍靠整机 reboot 规避 tap-reset。
SIGKILL、断电和存储损坏不保证异常恢复执行，也不保证 checkout 原子性。

## 回滚

等设备构建/消费任务结束、核对 state 确为本次事务后执行。可在 `rollback` 后传入
本次部署的源 SHA 进行额外身份检查（下面是本票示例）；省略则明确回滚当前 state。
SSH 返回成功只表示任务已启动：

```bash
ssh comma@10.0.0.27 '(setsid nohup /usr/local/venv/bin/python /data/build/.git/local-release.py rollback 20c9909ceb3a654fd9e51171f414edfb064ed5e4 > /tmp/rollback.log 2>&1 < /dev/null &)'
ssh comma@10.0.0.27 'tail -40 /tmp/rollback.log; /usr/local/venv/bin/python /data/build/.git/local-release.py status'
```

设备端校验当次保留的 rollback ref、保持更新禁用、停止 comma、
checkout 当次记录的旧 HEAD/分支、校验 HEAD/跟踪树、恢复原单键值、落盘
`rolled-back` 和本次 boot ID，最后才 reboot。
回滚保留部署时的 release SHA 供审计；不要把独立 reboot 接在失败的 checkout 后。
重连后确认 HEAD 和分支 ref 与 state 中 `original_commit`/`original_ref` 一致、
prebuilt 存在、manager 运行且没有 reset 界面，
DisableUpdates 与状态文件中的原值完全一致。回滚建议留 5–10 分钟。

## 宿主验证

```bash
python -m pytest -q tools/release
ruff check tools/release
ty check openpilot/common/pydeps.py tools/release/build_pydeps.py tools/release/local_release.py tools/release/release_lib.py
bash -n tools/release/device_release.sh tools/release/publish_release_from_device.sh
```

测试以真实临时 Git 仓库验证指定分支/固定对象、首次和连续部署、陈旧状态拒绝、
当次失败恢复和参数原值保留；服务、boot ID、重启和设备参数用替身。
它们不能代替批准后的真机 SCons、消费、回滚、reboot 和导航验收。
