from pathlib import Path

OPENPILOT = Path(__file__).resolve().parents[3]


def test_no_lateral_maneuver_event():
    src = (OPENPILOT / "selfdrive/selfdrived/selfdrived.py").read_text()
    assert "lateralManeuver" not in src.split("longitudinalManeuver")[0][-2000:]


def test_selfdrived_drops_lateral_maneuver_event_but_keeps_hook():
    src = (OPENPILOT / "selfdrive/selfdrived/selfdrived.py").read_text()
    assert "EventName.lateralManeuver" not in src
    assert "lateralManeuverPlan" in src
    assert "EventName.longitudinalManeuver" in src


def test_events_drops_lateral_maneuver_mapping_but_keeps_callback():
    src = (OPENPILOT / "selfdrive/selfdrived/events.py").read_text()
    assert "EventName.lateralManeuver" not in src
    assert "def longitudinal_maneuver_alert" in src
    assert "EventName.longitudinalManeuver" in src


def test_params_keys_drops_lateral_maneuver_mode():
    src = (OPENPILOT / "common/params_keys.h").read_text()
    assert "LateralManeuverMode" not in src
    assert "LongitudinalManeuverMode" in src
