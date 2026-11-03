from dataclasses import replace

import numpy as np
import pytest
import xarray as xr
from eo_pipe.io.memory import ChunkPlan
from eo_pipe.io.speckle import lee_block, quegan_block
from shapely.geometry import box

import reference_inputs as ri
from flood_monitoring import chunks, despeckle
from flood_monitoring.config import Despeckle

SIZE, SIDE, DATES = 48, 16, 6
SETTINGS = Despeckle(lee_window=5, quegan_window=3, enl=4.4)


@pytest.fixture
def plan_16(monkeypatch):
    calls = []

    def plan(budget_mb, bytes_per_pixel, height, width, stored=chunks.TILE):
        calls.append(budget_mb)
        return ChunkPlan(SIDE, 2)

    monkeypatch.setattr(chunks, "plan", plan)
    return calls


def write_cube(path, *, dates=DATES):
    ri.write_cube(path, size=SIZE, dates=dates, chunk=SIDE, flood_from=4, flood_fraction=0.5, salt_fraction=0.02)


def test_filtered_is_lee_then_quegan_on_linear_power_whatever_the_chunking(tmp_path):
    write_cube(tmp_path / "cube.nc")
    with xr.open_dataset(tmp_path / "cube.nc") as cube:
        db = cube["VV"].values
        out = despeckle.filtered(cube.chunk({"time": -1, "y": SIDE, "x": SIDE}), SETTINGS)["VV"].values
    power = np.power(np.float32(10), db / np.float32(10))
    lee = lee_block(power, window=5, enl=4.4, nodata=None)
    expected = 10 * np.log10(quegan_block(lee, window=3, nodata=None))
    assert np.array_equal(np.isnan(out), np.isnan(expected))
    np.testing.assert_allclose(out, expected, atol=1e-3, equal_nan=True)


def test_run_writes_an_int16_filtered_cube_and_plans_with_the_budget(tmp_path, plan_16):
    config, paths = ri.single_series_config(tmp_path, size=SIZE, dates=DATES, budget_mb=777)
    paths.interim.mkdir(parents=True)
    write_cube(paths.cube)
    despeckle.run(replace(config, aoi_polygon=None), paths)
    with xr.open_dataset(paths.filtered_cube) as ds:
        assert ds["VV"].encoding["dtype"] == "int16" and ds["VV"].dtype == np.float32
        assert ds.sizes["time"] == DATES
    assert plan_16 == [777, 777]
    assert not paths.window.exists()


def test_run_stores_only_the_chunks_that_touch_the_polygon(tmp_path, plan_16):
    config, paths = ri.single_series_config(tmp_path, size=SIZE, dates=DATES)
    # a polygon inside the chunk (row 1, column 2) of the 3 x 3 grid of chunks
    corner = box(
        ri.ORIGIN[0] + 2.2 * SIDE * ri.PIXEL, ri.ORIGIN[1] - 1.8 * SIDE * ri.PIXEL,
        ri.ORIGIN[0] + 2.8 * SIDE * ri.PIXEL, ri.ORIGIN[1] - 1.2 * SIDE * ri.PIXEL,
    )
    paths.interim.mkdir(parents=True)
    write_cube(paths.cube)
    despeckle.run(replace(config, aoi_polygon=ri._to_lonlat(corner)), paths)
    with np.load(paths.window) as saved:
        window, side = saved["window"], int(saved["side"])
    assert side == SIDE and window.sum() == 1 and window[1, 2]
    with xr.open_dataset(paths.filtered_cube) as ds:
        vv = ds["VV"].values
    inside = np.zeros((SIZE, SIZE), bool)
    inside[SIDE : 2 * SIDE, 2 * SIDE :] = True
    assert np.isnan(vv[:, ~inside]).all()
    assert np.isfinite(vv[:, inside]).any()


def test_quegan_needs_two_acquisitions(tmp_path):
    write_cube(tmp_path / "cube.nc", dates=1)
    with xr.open_dataset(tmp_path / "cube.nc") as cube, pytest.raises(ValueError, match="at least 2 Acquisitions"):
        despeckle.filtered(cube, SETTINGS)
