"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.base import BrandSettings
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.body import BodySettings
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.toyota import ToyotaSettings


class BrandSettingsFactory:
  _BRAND_MAP: dict[str, type[BrandSettings]] = {
    "body": BodySettings,
    "toyota": ToyotaSettings,
  }

  @staticmethod
  def create_brand_settings(brand: str) -> BrandSettings | None:
    cls = BrandSettingsFactory._BRAND_MAP.get(brand)
    return cls() if cls is not None else None
