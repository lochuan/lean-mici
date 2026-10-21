# 本地源车机发版

入口沿用 `publish_release_from_device.sh` 和 `device_release.sh`。不开
`--local-source` 时，主线发版的测试、远端 fetch、fork/gitee 推送、消费和重启顺序不变。
本地模式只传 Mac 本地已提交的源码和固定子模块对象，不推公共远端。

## 部署

仅在批准的驻车维护窗口、供电稳定且没有其他构建/部署任务时执行。命令会写设备、
停 comma、在 `/data/build` 强制 checkout/clean、使用 `/data/scons_cache` 构建，
剥成 `/data/relstage`，消费到 `/data/openpilot` 后整机重启；没有 dry-run。

```bash
EXPECTED_SOURCE_SHA=fb8b1d5a9c4391f4b047da88ad478d5bcf563ae0 \
DEVICE=comma@10.0.0.27 \
bash tools/release/publish_release_from_device.sh --local-source --skip-smoke
```

`EXPECTED_SOURCE_SHA` 必须是批准的完整 40 位 SHA，并与 `refs/heads/nav-test` 相同。
HEAD、同名 tag 和未提交的工作树修改不作为业务源码。发布分支固定为
`nav-local-release`，保留 `lean-release` 指向回滚目标
`e72bce67aff8b593f2946d367f14eb6195e03de3`。

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

本轮真机只读记录的原值是 `b'0'`。若状态文件丢失，自动回滚会拒绝猜测；
只有确认本次部署前仍为该值，才可把它作为人工恢复此单键的依据
（旧代码下 `Params().put_bool('DisableUpdates', False, block=True)`）。
必须先核实旧 release 的 HEAD、完整跟踪树与任务状态，不能借此跳过代码回滚，
也不能把这次的 `0` 用于其他部署。DisableUpdates 带 BACKUP 标志，
部署期间 sunnylink 的设置备份可能记下 `1`；后续恢复设置时要检查该键。

updated 只在启动时读 DisableUpdates；原构建停服后，新拉起的 updated 看到 `1`
会退出。停车期间 manager 可能约每秒两次重新拉起它，带来 CPU/进程创建和日志开销；
这是现有“禁用更新”设置的行为，未测量本车实际开销。行驶中 updated 不运行。
部署后只读观察停车时 CPU、updated PID 变化与日志；验收期间保持禁用更新，
避免不存在的远端 nav-local-release 分支报错或 OTA finalize 替换本地树。

## 轮询和失败处理

构建、消费、回滚都在设备端分离执行，不依赖 Mac SSH 持续连接。
消费先核对 fetch SHA 和 `source commit: nav-test@<SHA>`，停服并 checkout，
核对 HEAD/跟踪树后，**先持久写 `rebooting` 再请求 reboot**。
Mac 看到该状态后等待重连并做启动、SHA 和参数检查。

消费轮询为 72 次、间隔 5 秒，另有每次 SSH 连接耗时；“6 分钟”是轮询等待预算，
不是硬性墙钟截止、SCons 构建限时或设备任务限时。440 MB 级的 stage fetch 和
约 0.9 GB 的 checkout 能否在窗口内完成尚未实测。超时只是 Mac 未确认状态，
设备可能仍在消费并随后重启，Mac 会退出且不继续自动启动检查。
先查 `/tmp/consume.log` 和状态，不要另行重启或并发回滚。

消费/回滚失败会尝试恢复并验证 e72bce6、恢复 DisableUpdates、start comma，
并输出回滚命令；失败路径不自动 reboot。分开处理状态：

- `recovered`：旧树及原参数已验证恢复；确认没有任务执行后，普通整机重启是安全的，
  可清除 tap-reset 的触摸计数。也可执行下述 rollback，由它校验后重启。
  若要重新部署，必须先 rollback；只 reboot 不会清掉 `recovered` 状态。
- `failed`、`switching` 或状态不明：先排查日志、任务和树，禁止盲目 reboot。
  持续磁盘/权限故障时只能保证尝试 start comma，不能保证业务恢复。
- prepare 之前失败：提示未进入参数设置/设备构建，不输出本次回滚命令。
  prepare 已发起但 SSH 失败时，仍提示查状态后回滚，因为原值可能已落盘或参数已写入。

不创建 `/tmp/booted`；成功部署和回滚仍靠整机 reboot 规避 tap-reset。
SIGKILL、断电和存储损坏不保证异常恢复执行，也不保证 checkout 原子性。

## 回滚

等设备构建/消费任务结束后执行，SSH 返回成功只表示任务已启动：

```bash
ssh comma@10.0.0.27 '(setsid nohup /usr/local/venv/bin/python /data/build/.git/local-release.py rollback > /tmp/rollback.log 2>&1 < /dev/null &)'
ssh comma@10.0.0.27 'tail -40 /tmp/rollback.log; /usr/local/venv/bin/python /data/build/.git/local-release.py status'
```

设备端校验完整 `refs/heads/lean-release`、保持更新禁用、停止 comma、
checkout e72bce6、校验 HEAD/跟踪树、恢复原单键值、落盘 `rolled-back`，最后才 reboot。
回滚保留部署时的 release SHA 供审计；不要把独立 reboot 接在失败的 checkout 后。
重连后确认 HEAD=e72bce6、prebuilt 存在、manager 运行且没有 reset 界面，
DisableUpdates 与状态文件中的原值完全一致。回滚建议留 5–10 分钟。

## 宿主验证

```bash
python -m pytest -q tools/release
ruff check tools/release
bash -n tools/release/device_release.sh tools/release/publish_release_from_device.sh
```

测试以真实临时 Git 仓库验证对象/checkout 和失败恢复，服务、重启和设备参数用替身。
它们不能代替批准后的真机 SCons、消费、回滚、reboot 和导航验收。
