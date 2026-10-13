import pytest

from flood_monitoring.config import Detection, load_event_config

BASE = """\
project: p
event: e-2026-01
area_of_interest: [0, 0, 1, 1]
window: {start: 2026-01-01, end: 2026-02-01}
series:
  collection: c
  start: 2025-09-01
  end: 2026-02-01
  orbit_state: descending
  instrument_mode: IW
  relative_orbit: 1
  assets: [vv, vh]
"""


def load(tmp_path, extra=""):
    (tmp_path / "e-2026-01.yaml").write_text(BASE + extra)
    return load_event_config("e-2026-01", events_dir=tmp_path)


def test_detection_defaults_are_the_settled_rules(tmp_path):
    assert load(tmp_path).detection == Detection(8, 4.0, 0.01, -15.0, 0.5, 20, 8)


def test_detection_override(tmp_path):
    config = load(tmp_path, "detection:\n  water_db: -17\n  sieve_size: 50\n")
    assert config.detection.water_db == -17.0
    assert config.detection.sieve_size == 50
    assert config.detection.drop_db == 4.0


def test_unknown_detection_setting(tmp_path):
    with pytest.raises(ValueError, match="unknown detection setting.*otsu"):
        load(tmp_path, "detection:\n  otsu: true\n")
