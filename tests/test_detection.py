import numpy as np
import pytest

from flood_monitoring import detection as d


def series(values, shape=(10, 10)):
    """One constant-valued (y, x) frame per value in *values* (dB)."""
    return np.stack([np.full(shape, v, dtype="float32") for v in values])


def test_to_db_masks_nodata_nan_and_nonpositive():
    out = d.to_db(np.array([1.0, 0.1, -32768.0, np.nan, 0.0]), nodata=-32768.0)
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(-10.0)
    assert np.isnan(out[2:]).all()


def test_event_start_first_step_over_share():
    db = series([-8] * 9 + [-9, -14, -14])
    db[9, :5] = -14  # 50 % of pixels drop 6 dB at step 9; step 8 has no drop
    k, shares = d.event_start(db, min_baseline=8, drop_db=4, min_share=0.01)
    assert k == 9
    assert shares[0] == 0.0 and len(shares) == 2


def test_event_start_ignores_small_wet_episode():
    db = series([-8] * 12)
    db[9, 0, 0] = -20  # 1 % of 100 px is not "more than 1 %"
    db[11, :2, :] = -20  # 20 % at step 11
    k, _ = d.event_start(db, min_baseline=8, drop_db=4, min_share=0.01)
    assert k == 11


def test_event_start_baseline_is_median_of_earlier_steps():
    # a single outlier in the Baseline must not hide the drop
    db = series([-8] * 8 + [-8, -14])
    db[3] = 5.0
    k, _ = d.event_start(db, min_baseline=8, drop_db=4, min_share=0.01)
    assert k == 9


def test_event_start_too_short_series():
    with pytest.raises(ValueError, match="more than 8"):
        d.event_start(series([-8] * 8), 8, 4, 0.01)


def test_event_start_not_found():
    with pytest.raises(ValueError, match="no Event Start"):
        d.event_start(series([-8] * 12), 8, 4, 0.01)


def test_event_start_nan_pixels_do_not_count():
    db = series([-8] * 10 + [-14])
    db[:, :, :5] = np.nan
    db[10, :, 5:] = -14
    k, shares = d.event_start(db, 8, 4, 0.01)
    assert k == 10 and shares[-1] == pytest.approx(1.0)


def test_water_threshold_and_nan():
    db = np.array([-20.0, -15.0, -10.0, np.nan])
    assert d.water(db, -15.0).tolist() == [True, False, False, False]


def test_permanent_water_majority_of_pre_dates():
    pre = np.zeros((4, 1, 3), dtype=bool)
    pre[:, 0, 0] = True  # always water
    pre[:2, 0, 1] = True  # half: not "more than half"
    pre[:3, 0, 2] = True  # 3 of 4
    assert d.permanent_water(pre, 0.5)[0].tolist() == [True, False, True]


def test_flood_extent_and_peak():
    masks = np.zeros((3, 1, 5), dtype=bool)
    masks[0, 0, :2] = True
    masks[1, 0, :4] = True
    masks[2, 0, 1:3] = True
    perm = np.array([[True, False, False, False, False]])
    extent = d.flood_extent(masks, perm)
    assert extent[0].tolist() == [False, True, True, True, False]
    i, areas = d.peak(masks, perm)
    assert i == 1 and areas == [1, 3, 2]
