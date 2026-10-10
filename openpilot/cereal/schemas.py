"""cereal capnp schema 清单：SConscript 由此推导 capnpc 的构建输入与生成目标。

路径是仓库相对路径，SCons 的 '#' 锚形由 SConscript 自行拼接。
"""
from __future__ import annotations

SCHEMAS: tuple[str, ...] = (
  "openpilot/cereal/log.capnp",
  "openpilot/cereal/deprecated.capnp",
  "openpilot/cereal/custom.capnp",
  "opendbc_repo/opendbc/car/car.capnp",
)
