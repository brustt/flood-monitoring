import warnings

import numpy as np
import pytest
import xarray as xr

from flood_monitoring import detection as d


def series(values, shape=(10, 10)):
    """One constant-valued (y, x) frame per value in *values* (dB)."""
    return np.stack([np.full(shape, v, dtype="float32") for v in values])


def lazy(db, side=None):
    """(time, y, x) DataArray, chunked spatially by *side* (whole grid when None)."""
    da = xr.DataArray(db, dims=("time", "y", "x"))
    return da.chunk({"time": -1, "y": side or -1, "x": side or -1})


def mask(values):
    return xr.DataArray(np.asarray(values, dtype=bool), dims=("y", "x"))


def test_to_db_masks_nodata_nan_and_nonpositive():
    out = d.to_db(np.array([1.0, 0.1, -32768.0, np.nan, 0.0]), nodata=-32768.0)
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(-10.0)
    assert np.isnan(out[2:]).all()


def test_to_db_lazy_drops_variable_attrs_keeps_crs():
    power = xr.DataArray(np.full((2, 3), 0.1, "float32"), dims=("y", "x"), coords={"y": [1.5, 0.5], "x": [0.5, 1.5, 2.5]})
    power = power.rio.write_crs("EPSG:32630").chunk()
    power.attrs["nodata"] = -32768.0
    db = d.to_db_lazy(power, -32768.0)
    assert db.rio.crs == power.rio.crs
    assert "nodata" not in db.attrs
    np.testing.assert_allclose(db.values, -10.0, atol=1e-5)


def test_event_start_first_step_over_share():
    db = series([-8] * 9 + [-9, -14, -14])
    db[9, :5] = -14  # 50 % of pixels drop 6 dB at step 9; step 8 has no drop
    k, shares = d.event_start(lazy(db), min_baseline=8, drop_db=4, min_share=0.01)
    assert k == 9
    assert shares[0] == 0.0 and len(shares) == 2


def test_event_start_ignores_small_wet_episode():
    db = series([-8] * 12)
    db[9, 0, 0] = -20  # 1 % of 100 px is not "more than 1 %"
    db[11, :2, :] = -20  # 20 % at step 11
    k, _ = d.event_start(lazy(db), min_baseline=8, drop_db=4, min_share=0.01)
    assert k == 11


def test_event_start_baseline_is_median_of_earlier_steps():
    # a single outlier in the Baseline must not hide the drop
    db = series([-8] * 8 + [-8, -14])
    db[3] = 5.0
    k, _ = d.event_start(lazy(db), min_baseline=8, drop_db=4, min_share=0.01)
    assert k == 9


def test_event_start_too_short_series():
    with pytest.raises(ValueError, match="need more than 8 Acquisitions to detect the Event Start, got 8"):
        d.event_start(lazy(series([-8] * 8)), 8, 4, 0.01)


def test_event_start_not_found():
    with pytest.raises(ValueError, match="no Event Start"):
        d.event_start(lazy(series([-8] * 12)), 8, 4, 0.01)


def test_event_start_nan_pixels_do_not_count():
    db = series([-8] * 10 + [-14])
    db[:, :, :5] = np.nan
    db[10, :, 5:] = -14
    k, shares = d.event_start(lazy(db), 8, 4, 0.01)
    assert k == 10 and shares[-1] == pytest.approx(1.0)


def growing_flood():
    """20 steps; from step 15 the flood covers a third of the pixels."""
    db = series([-8] * 20, shape=(9, 9))
    db[15:, :3] = -14
    return db


@pytest.mark.parametrize("batch", [1, 3, 8, 50])
def test_event_start_same_result_for_any_batch(batch):
    k, shares = d.event_start(lazy(growing_flood(), 4), 8, 4, 0.01, batch=batch)
    assert k == 15
    assert shares == [0.0] * 7 + [pytest.approx(1 / 3)]


def test_event_start_stops_after_the_batch_holding_k(monkeypatch):
    calls = []
    real = d.series_drop_counts

    def counted(db, candidates, *args, **kwargs):
        calls.append(candidates)
        return real(db, candidates, *args, **kwargs)

    monkeypatch.setattr(d, "series_drop_counts", counted)
    k, shares = d.event_start(lazy(growing_flood()), 8, 4, 0.01, batch=3)
    assert k == 15 and len(shares) == 8
    assert calls == [range(8, 11), range(11, 14), range(14, 17)]


def test_drop_counts_only_inside_the_area_of_interest():
    db = np.zeros((3, 1, 4), dtype="float32")
    db[2, 0, :2] = -10  # a Drop of 10 dB on two pixels
    inside = np.array([[True, False, True, True]])
    assert d.drop_counts(db, range(2, 3), 2, 4.0).tolist() == [[4, 2]]  # share 0.5
    assert d.drop_counts(db, range(2, 3), 2, 4.0, inside).tolist() == [[3, 1]]  # share 1/3


@pytest.mark.parametrize("side", [3, 5, None])
def test_series_drop_counts_is_chunk_independent(side):
    rng = np.random.default_rng(1)
    db = (rng.random((12, 11, 11)) * -20).astype("float32")
    db[:, 2, :] = np.nan
    inside = np.zeros((11, 11), bool)
    inside[1:9, 2:10] = True  # cut by the chunk edges of sides 3 and 5
    expected = d.drop_counts(db, range(8, 12), 8, 4.0, inside)
    got = d.series_drop_counts(lazy(db, side), range(8, 12), 8, 4.0, mask(inside).chunk(side or -1)).compute()
    assert got.tolist() == expected.tolist()


def test_series_drop_counts_too_short_raises_before_compute():
    with pytest.raises(ValueError, match="more than 8"):
        d.series_drop_counts(lazy(series([-8] * 8)), range(8, 9), 8, 4.0)


def test_drop_counts_emits_no_warning():
    db = series([-8] * 10)
    db[:, 0, :] = np.nan  # all-NaN pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        d.drop_counts(db, range(8, 10), 8, 4.0)


def test_water_threshold_and_nan():
    db = xr.DataArray([-20.0, -15.0, -10.0, np.nan])
    assert d.water(db, -15.0).values.tolist() == [True, False, False, False]


def test_permanent_water_majority_of_pre_dates():
    pre = np.zeros((4, 1, 3), dtype=bool)
    pre[:, 0, 0] = True  # always water
    pre[:2, 0, 1] = True  # half: not "more than half"
    pre[:3, 0, 2] = True  # 3 of 4
    out = d.permanent_water(xr.DataArray(pre, dims=("time", "y", "x")), 0.5)
    assert out.dtype == bool and out.values[0].tolist() == [True, False, True]


def test_permanent_water_needs_pre_event_dates():
    with pytest.raises(ValueError, match="no pre-event"):
        d.permanent_water(xr.DataArray(np.zeros((0, 1, 1), bool), dims=("time", "y", "x")), 0.5)


def test_flood_extent_flooded_areas_and_peak():
    masks = np.zeros((3, 1, 5), dtype=bool)
    masks[0, 0, :2] = True
    masks[1, 0, :4] = True
    masks[2, 0, 1:3] = True
    masks = xr.DataArray(masks, dims=("time", "y", "x"))
    perm = mask([[True, False, False, False, False]])
    extent = d.flood_extent(masks, perm)
    assert extent.dtype == bool and extent.values[0].tolist() == [False, True, True, True, False]
    areas = d.flooded_areas(masks, perm).values.tolist()
    assert areas == [1, 3, 2]
    assert d.peak(areas) == 1
