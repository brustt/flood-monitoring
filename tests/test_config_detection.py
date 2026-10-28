import pytest

from flood_monitoring.config import Detection, Processing, load_event_config

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


def test_processing_defaults(tmp_path):
    assert load(tmp_path).processing == Processing(4096.0)


def test_processing_budget_is_read(tmp_path):
    assert load(tmp_path, "processing:\n  memory_budget_mb: 2048\n").processing.memory_budget_mb == 2048.0


def test_processing_null_means_defaults(tmp_path):
    assert load(tmp_path, "processing:\n").processing == Processing()


def test_processing_rejects_non_positive_budget(tmp_path):
    with pytest.raises(ValueError, match="memory_budget_mb must be positive"):
        load(tmp_path, "processing:\n  memory_budget_mb: 0\n")


def test_processing_rejects_non_numeric_budget(tmp_path):
    with pytest.raises(ValueError, match="memory_budget_mb must be a number"):
        load(tmp_path, "processing:\n  memory_budget_mb: lots\n")


def test_processing_rejects_infinite_budget(tmp_path):
    with pytest.raises(ValueError, match="memory_budget_mb must be finite"):
        load(tmp_path, "processing:\n  memory_budget_mb: .inf\n")


def test_processing_rejects_unknown_key(tmp_path):
    with pytest.raises(ValueError, match=r"unknown processing setting\(s\) \['workers'\]"):
        load(tmp_path, "processing:\n  workers: 4\n")


def test_vector_file_yields_polygon_and_bounding_box(tmp_path):
    (tmp_path / "zone.geojson").write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[0,45],[0.1,45],[0.1,45.1],[0,45]]]}},'
        '{"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[0.2,45],[0.3,45],[0.3,45.2],[0.2,45]]]}}]}'
    )
    text = BASE.replace("area_of_interest: [0, 0, 1, 1]", "area_of_interest: zone.geojson")
    config = load_with_events(tmp_path, text)
    assert config.area_of_interest == pytest.approx((0.0, 45.0, 0.3, 45.2))
    assert config.aoi_polygon.geom_type == "MultiPolygon"
    assert config.aoi_polygon.bounds == pytest.approx(config.area_of_interest)


def test_vector_file_is_reprojected_to_lonlat(tmp_path):
    (tmp_path / "zone.geojson").write_text(
        '{"type":"FeatureCollection","crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:EPSG::32630"}},'
        '"features":[{"type":"Feature","properties":{},"geometry":{"type":"Polygon",'
        '"coordinates":[[[500000,5000000],[501000,5000000],[501000,5001000],[500000,5000000]]]}}]}'
    )
    text = BASE.replace("area_of_interest: [0, 0, 1, 1]", "area_of_interest: zone.geojson")
    west, south, east, north = load_with_events(tmp_path, text).area_of_interest
    assert -3.01 < west < east < -2.98 and 45.1 < south < north < 45.2


def test_vector_file_without_polygons_is_rejected(tmp_path):
    (tmp_path / "river.geojson").write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
        '"geometry":{"type":"LineString","coordinates":[[0,45],[0.1,45]]}}]}'
    )
    text = BASE.replace("area_of_interest: [0, 0, 1, 1]", "area_of_interest: river.geojson")
    with pytest.raises(ValueError, match="polygon"):
        load_with_events(tmp_path, text)


def test_missing_vector_file_is_rejected(tmp_path):
    text = BASE.replace("area_of_interest: [0, 0, 1, 1]", "area_of_interest: nope.gpkg")
    with pytest.raises(FileNotFoundError, match="nope.gpkg"):
        load_with_events(tmp_path, text)
