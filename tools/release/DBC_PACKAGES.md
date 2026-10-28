# Sienna Patched LTA DBC 发布与验证

正式 `lean-master → lean-release` 发布会在 R2 为空、包文件缺失或批准源提交变化时自动构建并上传 DBC；同源重复发布跳过重建。`--local-source` 和其他分支不访问生产密钥服务或 R2。

保护限度：其他 Toyota 使用的共享 DBC 明文继续保留，且内容与本包资产相同。这套机制提供受支持运行路径上的激活门槛，**不提供 DBC 内容保密性**，也不能阻止拥有修改程序能力的人绕过授权。设备软件包装同样不能抵抗 root。

## 构建

在固定、干净的源码版本上执行；`--source-sha` 指的是 sunnypilot 源提交，不是扁平 release 提交。依赖使用项目的 opendbc、pycryptodome、zstandard。

构建入口强制验证 HEAD 与本地 lean-master 的实际提交都等于 `--source-sha`，opendbc_repo HEAD 与该提交的 gitlink 相同，并拒绝主树/子树的已跟踪修改或未跟踪文件；读取源资产前后各校验一次。输出与私有密钥应放在源码树外。当前未提交的工作区不能用作正式发布包来源。测试包使用明确的编码测试 adapter，生产 CLI 没有跳过校验的选项。

```bash
python tools/release/dbc_package.py \
  --source-sha <40位小写源码SHA> \
  --data-date 2026-10-28 \
  --output /tmp/sienna-public \
  --private-key /显式指定的私有目录/sienna-key.json
```

输出目录必须为空，私有密钥路径必须在输出目录以外，已有密钥记录拒绝覆盖。公共目录恰好含 `manifest.json`、`package.enc`；密钥记录以 0600 原子写入独立私有路径，不能上传公共桶。不会打印密钥。

## lean-release 自动发布

`publish_release_from_device.sh` 先读取 R2 全局 latest，并检查当前 source 的 latest、manifest 和密文是否存在。批准源提交固定了 opendbc gitlink，因此 source 变化同时覆盖 opendbc/DBC 更新；即使只是其他 lean 代码变化，也必须生成新 source 的 manifest，才能通过设备端 SOURCE_SHA 门禁。

需要已有发布配置：`DOWNLOAD_BASE_URL` 与 R2 凭据、`DEPLOY_SSH_ALIAS`、`DEPLOY_ROOT`、`AGE_RECIPIENT`；`TOYOCHIP_SERVER_ROOT` 缺省为 `sunnypilot` 相邻的 `toyochip-server`。设备在干净构建树中生成公共包与独立私钥记录，Mac 取回后依次执行密钥导入、加密备份和候选记录恢复验证。fork/Gitee 推送成功后才上传 R2 并清理旧版本，再由车机消费新 release，避免推送失败使线上只剩未发布源码的包。R2 阶段失败不消费新 release；重试会复用设备上同源的未完成的包，不重复生成密钥。

构建器读取 `DBC[CAR.TOYOTA_SIENNA_PATCHED_LTA]` 的 pt/radar 依赖，映射如下：

| 专用 name | source |
| --- | --- |
| toyota_sienna_patched_lta_pt | toyota_secoc_pt_generated |
| toyota_sienna_patched_lta_adas | toyota_tss2_adas |

pt 使用原生成器的完整 UTF-8 输出（含 import 展开）；adas 使用原 DBC 文件原字节。明文按上表顺序直接连接，没有 tar 时间戳、权限或随机元数据。相同输入的明文和 zstd 编码确定；每次发布仍独立随机生成 32 字节 AES key、16 字节 key_id 和 4 字节 file_id，因此密文本身每次不同。不要复用 key/nonce。

## 精确格式

JSON 使用缩进 2 与末尾换行。manifest 恰好包含：

```text
format = chipmunk-dbc-aes256gcm-v1
namespace = dbc
id = <source_sha>-<key_id>
platform = TOYOTA_SIENNA_PATCHED_LTA
source_sha = 40 lowercase hex
key_id = 32 lowercase hex
data_date = YYYY-MM-DD（有效日期）
chunk_size = 1048576
tag_size = 16
nonce_scheme = file-id32-counter64-be
package = {file: package.enc, size, sha256}
plaintext = {size, sha256}
assets = [{name, source, size, sha256}, {name, source, size, sha256}]
```

assets 顺序固定为 pt、adas。密钥记录恰好是服务器现有九字段：`namespace,id,key_format,algorithm,key_id,data_date,manifest_sha256,key_wrap,key_b64`，其中 key_format=1、algorithm=AES-256-GCM、key_wrap=none；manifest_sha256 绑定 manifest 原字节。manifest 不含 key_b64。JSON 重复字段、多余字段、错误字段类型、未知资产、无效日期或大小均拒绝。

`package.enc` 二进制编码：

1. 37 字节头：`CHDBCENC\x01`（9）+ key_id 原始字节（16）+ file_id（4）+ zstd 压缩长度 uint64 big-endian（8）。
2. 压缩流按最多 1 MiB 分块，每块后紧跟 16 字节 GCM tag，没有额外 padding 或尾随数据。
3. nonce 为 file_id（4）+ 从 0 开始的块序号 uint64 big-endian（8）。
4. AAD 为 ASCII `CHDBCENC-BLOCK-v1` + 完整头（37）+ 块序号 uint64 big-endian + 本块压缩明文长度 uint32 big-endian。

解密逐块认证，全部认证后再做单帧 zstd 解压，验证总明文及两项资产 SHA-256 后才向调用方返回资产。受保护包与明文各限 16 MiB，manifest 限 16 KiB，密钥响应限 4 KiB；扩容需同时评审构建器、下载器与解码上限。zstd 帧中声明大小必须与 manifest 一致，额外帧/尾随字节拒绝。

设备实际加载在解密前还会读取当前硬件序列号，并要求与私有 state.device_id 相同；未知、读取失败或默认 PC 环境拒绝加载。整份资源目录被复制到其他序列号设备也不能直接调用 `DBC(name)` 使用。该软件检查不改变 AGNOS 无法抵抗 root 的保护限度。

## R2 布局与回滚

```text
dbc/<id>/manifest.json
dbc/<id>/package.enc
dbc/sienna-patched-lta/<source_sha>/latest.json
```

source 专用 `latest.json` 是 **manifest 的完整原字节副本**，沿用现有 R2 发布器格式，不是额外三字段对象。设备先解析 latest，再读取 id 下的 manifest 并要求原字节相同；两者 source_sha 必须等于运行树根目录的 SOURCE_SHA。只有包大小、包 SHA 与 manifest 校验通过后，才向固定 HTTPS 激活 origin 请求 `/keys/dbc/<id>`，核对九字段密钥记录并解密验证。

R2 只保留当前版本：上述两项版本对象、当前 source latest 和全局 latest 共四项。新包与指针全部写成功后才删除其他 `dbc/` 对象，不影响 AGNOS 或 Chipmunk OS。

`device_release.sh` 将真实 SRC_SHA 写入 stage/SOURCE_SHA；`release_lib.check_stage` 要求其格式正确、匹配指定源码 SHA，且与 RELEASE_MANIFEST.json.source_commit 一致。不要手写一个不同的 release HEAD 冒充源码 SHA。

切回旧运行树后，monitor 检测 SOURCE_SHA 变化并重建资源上下文。加载器按当前 source 在 releases 中选择并完整认证已安装包；current 只是优先指针，不会遮蔽旧 source 的包，因此 A→B→离线回滚 A 可直接使用已有 A 包。R2 不保留历史包：若回滚 source 在设备上没有已安装且完整的包，需要先重新发布该 source 的包；缺包期间 card 留在进程内等待，不改选平台、不进入控制初始化。

## 验证与剩余验收

聚焦验证入口：

```bash
PYTHONPATH=.:opendbc_repo python -m pytest \
  tools/release/test_dbc_package.py tools/release/test_release_lib.py \
  opendbc_repo/opendbc/can/tests/test_dbc_asset.py \
  opendbc_repo/opendbc/car/toyota/tests/test_toyota.py \
  openpilot/system/lanlinkd/tests/test_dbc_activation.py \
  openpilot/system/lanlinkd/tests/test_vehicle_api.py \
  openpilot/system/lanlinkd/tests/test_lanlinkd_app.py
cd openpilot/system/lanlinkd/web
npm test -- tests/siennaActivation.test.ts
npm run typecheck
```

Toyota、opendbc/车机通用 car interfaces 和 Toyota safety 测试显式选择 tests/dbc_adapter.py；它只在测试中替换受保护加载接口，生产无环境变量/明文绕过。stage 仅剥除 adapter 与四个引用它的受保护测试文件，保留 tests/car_diff.py 等 process_replay/diff_report 运行时依赖；发布门禁额外拒绝 adapter 残留，组装测试核对 diff_report 所需导出。包接口测试使用真实临时加密包；独立 wire encoder 覆盖跨 1 MiB 分块的 nonce/AAD、完整性与 zstd 尾随拒绝。Cabana 采用明确的开发映射指向共享源 DBC，不改变运行时专用名字的加载门禁。

本次车机 `comma@10.0.0.27` SSH 超时，尚未在真机执行离线包加载、行驶/停车切换、断电续清、LanLink 关闭后 monitor 存活与资源清理验收。主机测试不代替这些检查。未运行全量构建、全量测试或全量格式化；只构建了 LanLink 前端目标以更新可运行静态资源。

本次未暂存或提交源码；static/.build-hash 依照现有工具绑定 Git 索引，后续正式暂存全部前端源码后，必须重新执行 `tools/lanlink/build_lanlink_web.py --build` 更新索引指纹，再正常评审与发布。本次不以旧索引指纹证明未提交源码的发布就绪状态。
