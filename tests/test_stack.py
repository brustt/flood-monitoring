from datetime import date

import numpy as np
import xarray as xr

from flood_monitoring import stack
from flood_monitoring.config import Series
from flood_monitoring.cube import DB_SCALE

SERIES = Series("sentinel-1-rtc", date(2026, 1, 1), date(2026, 3, 1), "descending", "IW", (8,), ("vv",))


def touch(raw, day, asset):
    tif = raw / "sentinel-1-rtc" / day / "item" / f"item_{asset}_sub-abc.tif"
    tif.parent.mkdir(parents=True, exist_ok=True)
    tif.touch()
    return tif


def test_subsets_keep_only_the_series_dates_and_assets(tmp_path):
    kept = [touch(tmp_path, "2026-01-01", "vv"), touch(tmp_path, "2026-02-10", "vv"), touch(tmp_path, "2026-03-01", "vv")]
    touch(tmp_path, "2025-12-31", "vv")
    touch(tmp_path, "2026-03-02", "vv")
    touch(tmp_path, "2026-02-10", "vh")

    assert stack.subsets(tmp_path, SERIES) == sorted(kept)


def stacked_power(path, size=64, dates=3):
    """What stack_tiles writes: float32 linear power with nodata as the _FillValue."""
    rng = np.random.default_rng(1)
    power = rng.uniform(0.01, 0.3, (dates, size, size)).astype("float32")
    power[0, :4, :4] = -32768.0
    ds = xr.Dataset(
        {n: (("time", "y", "x"), power) for n in ("VV", "VH")},
        coords={"time": np.arange(dates), "y": np.arange(float(size)), "x": np.arange(float(size))},
    )
    for var in ds.data_vars:
        ds[var].encoding.update(_FillValue=-32768.0, chunksizes=(dates, 32, 32))
    ds.to_netcdf(path)
    return power


def test_write_cube_converts_power_to_int16_db_and_stores_only_the_window(tmp_path):
    power = stacked_power(tmp_path / "stack.nc")
    window = np.array([[True, False], [False, True]])
    params = {"chunk_side": 32, "window": window}

    stored, total = stack._write_cube(tmp_path / "stack.nc", tmp_path / "cube.nc", params, budget=512)

    assert (stored, total) == (2, 4)
    with xr.open_dataset(tmp_path / "cube.nc") as ds:
        assert ds["VV"].encoding["dtype"] == "int16"
        db = ds["VV"].values
    assert np.isnan(db[0, :4, :4]).all()  # nodata
    assert np.isnan(db[:, :32, 32:]).all() and np.isnan(db[:, 32:, :32]).all()  # outside the window
    np.testing.assert_allclose(db[1, :32, :32], 10 * np.log10(power[1, :32, :32]), atol=DB_SCALE / 2 + 1e-4)


def test_write_cube_without_a_window_stores_every_chunk(tmp_path):
    stacked_power(tmp_path / "stack.nc")
    stored, total = stack._write_cube(tmp_path / "stack.nc", tmp_path / "cube.nc", {}, budget=512)
    assert stored == total
    with xr.open_dataset(tmp_path / "cube.nc") as ds:
        assert np.isfinite(ds["VV"].values[1]).all()
