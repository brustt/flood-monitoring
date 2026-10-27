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


def test_composite_ranges_default_and_override(tmp_path):
    assert load(tmp_path).composite.vv == (-20.0, -3.0)
    cfg = load(tmp_path, "composite:\n  vh: [-30, -10]\n")
    assert cfg.composite.vh == (-30.0, -10.0)
    assert cfg.composite.as_dict()["vv"] == (-20.0, -3.0)


def test_composite_range_must_be_low_then_high(tmp_path):
    with pytest.raises(ValueError, match="vv"):
        load(tmp_path, "composite:\n  vv: [0, -5]\n")


def test_single_relative_orbit_still_loads(tmp_path):
    config = load(tmp_path)
    assert config.series.relative_orbits == (1,)
    assert config.aoi_polygon is None
    assert config.area_of_interest == (0.0, 0.0, 1.0, 1.0)


def test_list_of_relative_orbits(tmp_path):
    config = load_with_events(tmp_path, BASE.replace("relative_orbit: 1", "relative_orbit: [8, 110]"))
    assert config.series.relative_orbits == (8, 110)


def test_ascending_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="descending"):
        load_with_events(tmp_path, BASE.replace("descending", "ascending"))


def load_with_events(tmp_path, text):
    (tmp_path / "events").mkdir(exist_ok=True)
    (tmp_path / "events" / "e-2026-01.yaml").write_text(text)
    return load_event_config("e-2026-01", events_dir=tmp_path / "events")


def test_river_buffer_yields_polygon_and_bounding_box(tmp_path):
    (tmp_path / "river.geojson").write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
        '"geometry":{"type":"LineString","coordinates":[[0,45],[0.1,45]]}}]}'
    )
    text = BASE.replace(
        "area_of_interest: [0, 0, 1, 1]", "area_of_interest: {line: river.geojson, buffer_m: 1000}"
    )
    config = load_with_events(tmp_path, text)
    west, south, east, north = config.area_of_interest
    assert config.aoi_polygon.bounds == pytest.approx((west, south, east, north))
    assert west < 0 and east > 0.1  # buffered past the line ends
    assert 0.008 < north - 45 < 0.01  # about 1 km of latitude


def test_missing_river_line_file_is_rejected(tmp_path):
    text = BASE.replace("area_of_interest: [0, 0, 1, 1]", "area_of_interest: {line: nope.geojson, buffer_m: 500}")
    with pytest.raises(FileNotFoundError, match="nope.geojson"):
        load_with_events(tmp_path, text)
