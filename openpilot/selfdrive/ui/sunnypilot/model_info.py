"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.models.fetcher import get_cached_bundles
from openpilot.sunnypilot.models.helpers import get_active_source, get_selected_bundle, resolve_bundle_by_ref
from openpilot.sunnypilot.models.model_name import DEFAULT_MODEL


def active_source() -> str:
  return get_active_source()


def bundles_for_source(source: str):
  if source == active_source():
    return ui_state.sm["modelManagerSP"].availableBundles
  return get_cached_bundles(ui_state.params, source)


def default_model(source: str) -> str:
  return DEFAULT_MODEL


def default_model_name(source: str) -> str:
  return f"{default_model(source)} (Default)"


def carrying_model() -> tuple[str | None, str | None, str | None]:
  """(source, internal name, display name) of what actually drives."""
  bundle = get_selected_bundle(ui_state.params, "qcom")
  if bundle:
    return "qcom", bundle.internalName, bundle.displayName
  name = default_model_name("qcom")
  return "qcom", name, name


def queued_name(current_ref) -> str | None:
  ref = ui_state.params.get("ModelManager_DownloadRef")
  if ref and ref != current_ref:
    source_bundles = {"qcom": bundles_for_source("qcom")}
    if resolved := resolve_bundle_by_ref(ref, source_bundles):
      return resolved[0].internalName
  return None


def model_info() -> tuple[str, str, str]:
  """returns (active source, active model name, other model name)"""
  source = active_source()
  active_bundle = get_selected_bundle(ui_state.params, source)
  active_name = active_bundle.displayName if active_bundle else default_model_name(source)
  return source, active_name, ""
