"""Manager-owned monitor; licensing must continue when LanLinkEnabled is false."""
import time


def main():
  from openpilot.common.params import Params
  from openpilot.system.lanlinkd.dbc_resources import create_resources
  from opendbc.can.dbc_asset import running_source_sha
  from openpilot.common.swaglog import cloudlog

  params = Params()
  resources = None
  next_check = 0.0
  next_warning = 0.0
  while True:
    try:
      if resources is None or resources.store.revision != running_source_sha():
        resources = create_resources(params)
        next_check = 0.0
      now = time.monotonic()
      check_token = now >= next_check
      resources.refresh(check_token=check_token)
      if check_token:
        next_check = now + 300
    except Exception:
      # Fixed message only: exception bodies can contain HTTP credentials. Persisted pending survives restart.
      if time.monotonic() >= next_warning:
        cloudlog.warning('DBC monitor unavailable; retrying persisted cleanup and authorization checks')
        next_warning = time.monotonic() + 300
    time.sleep(5)


if __name__ == '__main__':
  main()
