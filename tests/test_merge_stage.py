import json
from dataclasses import replace

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from flood_monitoring import merge_stage
from flood_monitoring.composite import NODATA
from flood_monitoring.config import EventGrid, load_event_config
from flood_monitoring.paths import EventPaths

CRS = "EPSG:32630"
SIZE = 40  # pixels of 10 m
TOP_LEFT = (500000, 5100000)


def write(path, data, nodata):
    bands = data if data.ndim == 3 else data[None]
    with rasterio.open(
        path, "w", driver="GTiff", width=bands.shape[2], height=bands.shape[1], count=len(bands),
        dtype=bands.dtype, crs=CRS, transform=from_origin(*TOP_LEFT, 10, 10), nodata=nodata,
    ) as dst:
        dst.write(bands)


def lonlat_box():
    t = Transformer.from_crs(CRS, 4326, always_xy=True)
    west, north = t.transform(*TOP_LEFT)
    east, south = t.transform(TOP_LEFT[0] + SIZE * 10, TOP_LEFT[1] - SIZE * 10)
    return west, south, east, north


@pytest.fixture
def setup(tmp_path):
    base = load_event_config("saintes-2026-02")
    config = replace(
        base,
        area_of_interest=lonlat_box(),
        series=replace(base.series, relative_orbits=(8, 110)),
        event_grid=EventGrid(crs=CRS, pixel_size=10.0),
    )
    paths = EventPaths(event=config.event, data_root=tmp_path / "data", repo_root=tmp_path / "repo")
    paths.ensure()

    def series(orbit, valid_cols, flooded_cols, before_value, peak):
        p = paths.series_paths(orbit)
        p.ensure()
        valid = np.zeros((SIZE, SIZE), bool)
        valid[:, valid_cols] = True

        def comp(value):
            return np.where(valid, np.float32(value), np.float32(NODATA))[None].repeat(3, 0)

        flooded = np.zeros((SIZE, SIZE), "uint8")
        flooded[10:20, flooded_cols] = 1
        write(p.flood_extent, flooded, None)
        write(p.permanent_water, np.zeros((SIZE, SIZE), "uint8"), None)
        write(p.before_composite, comp(before_value), NODATA)
        write(p.after_composite, comp(before_value - 1), NODATA)
        p.summary.write_text(
            json.dumps(
                {
                    "event_start": "2026-02-05",
                    "before": "2026-01-30",
                    "peak": peak,
                    "flooded_area_km2": {peak: 0.1},
                    "flood_extent_km2": 0.1,
                }
            )
        )

    series(8, slice(0, 30), slice(2, 10), -10.0, "2026-02-20")  # Main Series: 75% of the AOI
    series(110, slice(20, 40), slice(25, 35), -20.0, "2026-02-22")  # covers the east edge
    return config, paths


def test_merge_writes_global_layers_orbit_map_and_summary(setup):
    config, paths = setup
    merge_stage.run(config, paths)

    with rasterio.open(paths.flood_extent) as src:
        extent = src.read(1)
        assert src.nodata == 255 and src.crs.to_string() == CRS
    assert extent[15, 5] == 1 and extent[15, 30] == 1  # flooded in either Series
    assert extent[30, 5] == 0

    with rasterio.open(paths.orbit_map) as src:
        orbit_map = src.read(1)
    assert orbit_map[20, 5] == 1 and orbit_map[20, 35] == 2  # Main Series, then the fill
    with rasterio.open(paths.before_composite) as src:
        before = src.read(1)
    assert before[20, 5] == pytest.approx(-10) and before[20, 35] == pytest.approx(-20)  # no averaging
    assert before[20, 25] == pytest.approx(-10)  # the overlap goes to the Main Series

    summary = json.loads(paths.summary.read_text())
    assert summary["main_series"] == 8 and summary["peak"] == "2026-02-20"
    assert [s["relative_orbit"] for s in summary["series"]] == [8, 110]
    assert [s["rank"] for s in summary["series"]] == [1, 2]
    assert summary["series"][0]["coverage_share"] > summary["series"][1]["coverage_share"]
    assert summary["flood_extent_km2"] > 0
    assert json.loads(paths.aoi_polygon.read_text())["geometry"]["type"] == "Polygon"


def test_merge_skips_when_done_and_a_single_series_is_left_alone(setup, capsys):
    config, paths = setup
    merge_stage.run(config, paths)
    capsys.readouterr()
    merge_stage.run(config, paths)
    assert "skip" in capsys.readouterr().out
    merge_stage.run(replace(config, series=replace(config.series, relative_orbits=(8,))), paths, force=True)
    assert "single Series" in capsys.readouterr().out


def test_missing_series_result_names_the_orbit(setup):
    config, paths = setup
    paths.series_paths(110).flood_extent.unlink()
    with pytest.raises(FileNotFoundError, match="relative orbit 110"):
        merge_stage.run(config, paths)
