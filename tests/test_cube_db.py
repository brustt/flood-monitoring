import dask.array as da
import numpy as np
import xarray as xr

from flood_monitoring import cube

SIDE = 64


def dataset(db: np.ndarray, side: int = SIDE) -> xr.Dataset:
    """A dB float32 (time, y, x) VV/VH dataset chunked like a Cube."""
    t, h, w = db.shape
    coords = {"time": np.arange(t), "y": np.arange(float(h)), "x": np.arange(float(w))}
    arrays = {n: (("time", "y", "x"), da.from_array(db, chunks=(t, side, side))) for n in ("VV", "VH")}
    return xr.Dataset(arrays, coords=coords)


def sample(shape=(3, 2 * SIDE, 2 * SIDE)) -> np.ndarray:
    db = np.random.default_rng(0).normal(-15, 5, shape).astype("float32")
    db[0, 0, 0] = np.nan
    return db


def test_encode_rounds_to_hundredths_and_marks_nan():
    stored = cube.encode_db(np.array([-12.3456, np.nan, 0.0, -0.004], dtype="float32"))
    assert stored.dtype == np.int16
    assert stored.tolist() == [-1235, cube.DB_FILL, 0, 0]


def test_encode_never_produces_the_fill_value_for_data():
    assert cube.encode_db(np.array([-1000.0, 1000.0], dtype="float32")).tolist() == [-32767, 32767]


def test_db_to_power_inverts_to_db_and_keeps_nan():
    power = cube.db_to_power(np.array([-10.0, 0.0, np.nan], dtype="float32"))
    assert power.dtype == np.float32
    np.testing.assert_allclose(power[:2], [0.1, 1.0], rtol=1e-6)
    assert np.isnan(power[2])


def test_written_cube_reads_back_as_float32_db_within_rounding(tmp_path):
    db = sample()
    cube.write_db_cube(dataset(db), tmp_path / "c.nc", window=None, workers=2)
    with xr.open_dataset(tmp_path / "c.nc") as ds:
        assert ds["VV"].dtype == np.float32
        assert ds["VV"].encoding["dtype"] == "int16"
        assert ds["VV"].encoding["zlib"]
        assert ds["VV"].attrs["units"] == "dB"
        back = ds["VV"].values
    assert np.array_equal(np.isnan(back), np.isnan(db))
    np.testing.assert_allclose(back, db, atol=cube.DB_SCALE / 2 + 1e-6, equal_nan=True)


def test_chunks_outside_the_window_are_not_stored(tmp_path):
    db = sample()
    window = np.array([[True, False], [False, False]])
    full, windowed = tmp_path / "full.nc", tmp_path / "windowed.nc"
    assert cube.write_db_cube(dataset(db), full, window=None, workers=2) == 4
    assert cube.write_db_cube(dataset(db), windowed, window=window, workers=2) == 1
    with xr.open_dataset(windowed) as ds:
        back = ds["VV"].values
    np.testing.assert_allclose(back[:, :SIDE, :SIDE], db[:, :SIDE, :SIDE], atol=cube.DB_SCALE, equal_nan=True)
    assert np.isnan(back[:, SIDE:, :]).all() and np.isnan(back[:, :, SIDE:]).all()
    assert windowed.stat().st_size < full.stat().st_size / 2


def test_writing_while_reading_a_netcdf_source_in_other_threads(tmp_path):
    """Without xarray's netCDF4 lock around the write this segfaults: HDF5 is not thread-safe."""
    side, n = 32, 8
    db = np.random.default_rng(1).uniform(-25, 0, (4, side * n, side * n)).astype("float32")
    source = dataset(db, side)
    for var in source.data_vars:
        source[var].encoding.update(chunksizes=(4, side, side), zlib=True)
    source.to_netcdf(tmp_path / "source.nc")
    for attempt in range(3):
        with xr.open_dataset(tmp_path / "source.nc", chunks={"time": -1, "y": side, "x": side}) as lazy:
            cube.write_db_cube(lazy, tmp_path / f"out{attempt}.nc", window=None, workers=8)
    with xr.open_dataset(tmp_path / "out2.nc") as ds:
        np.testing.assert_allclose(ds["VH"].values, db, atol=cube.DB_SCALE / 2 + 1e-6)


def test_check_cube_accepts_a_decoded_db_cube(tmp_path):
    cube.write_db_cube(dataset(sample()), tmp_path / "c.nc", window=None, workers=1)
    with xr.open_dataset(tmp_path / "c.nc", chunks={}) as ds:
        cube.check_cube(ds, nodata=None, expected=3)
