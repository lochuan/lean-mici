"""cereal capnp schema 的唯一登记表（fix/schema-registry ①）。

「cereal 有哪几个 schema、谁参与构建」此前登记三份——regen_cereal_gen.sh 的
数组、device_release.sh 门禁的显式参数、本目录 SConscript 的构建输入。漏改
一处不报错，只是发布物悄悄缺件或 regen 悄悄少生成。收拢到这一处：

- ``SConscript``（同包 import）推导构建输入与生成目标；
- ``tools/release/regen_cereal_gen.sh`` 经 ``release_lib.py schema-paths`` CLI 取；
- 发布门禁 ``check-schema-stamp`` 缺省参数走本表。

路径的**规范形是仓库相对路径**：发布侧的 $SRC 绝对形、SCons 的 '#' 锚形由
各消费方自行拼接——那是表示层差异，不是事实差异，不要为此分表。
"""
from __future__ import annotations

SCHEMAS: tuple[str, ...] = (
  "openpilot/cereal/log.capnp",
  "openpilot/cereal/deprecated.capnp",
  "openpilot/cereal/custom.capnp",
  "opendbc_repo/opendbc/car/car.capnp",
)
