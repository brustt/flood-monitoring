import numpy as np
import pytest
import xarray as xr

from flood_monitoring.composite import NODATA
from flood_monitoring.merge import (
    ORBIT_MAP_NODATA,
    coverage,
    fill_composites,
    merge_flood_extent,
    rank_series,
    valid_composite,
)


def yx(values):
    return xr.DataArray(np.array(values, dtype=bool), dims=("y", "x")).chunk()


AOI = yx([[1, 1, 1, 0]])


def comp(*values):
    """(band, 1, 4) Composite: each band holds *values* (NODATA marks no data)."""
    data = np.broadcast_to(np.array(values, dtype="float32"), (3, 1, len(values))).copy()
    return xr.DataArray(data, dims=("band", "y", "x")).chunk()


def test_valid_composite_needs_every_band():
    c = comp(-10, NODATA, -12, np.nan)
    c[1, 0, 2] = NODATA
    assert valid_composite(c).values.tolist() == [[True, False, False, False]]


def test_coverage_is_the_share_of_aoi_pixels():
    assert coverage([1, 2], 4) == [0.25, 0.5]
    with pytest.raises(ValueError, match="holds no pixel"):
        coverage([0], 0)


def test_most_coverage_ranks_first():
    assert rank_series([0.25, 0.5]) == [1, 0]


def test_ties_follow_config_order():
    assert rank_series([0.5, 0.5]) == [0, 1]


def test_flood_extent_is_flooded_where_any_series_is_flooded_inside_aoi():
    a = yx([[1, 0, 0, 1]])
    b = yx([[0, 1, 0, 1]])
    assert merge_flood_extent([a, b], AOI).values.tolist() == [[True, True, False, False]]


def test_composite_fill_takes_main_series_then_next_where_it_has_no_data():
    main = comp(-10, NODATA, -12, -13)
    other = comp(-20, -21, NODATA, -23)
    merged, orbit_map = fill_composites([other, main], [1, 0], AOI)
    assert merged.dims == ("band", "y", "x") and merged.dtype == np.float32
    assert orbit_map.dims == ("y", "x") and orbit_map.dtype == np.uint8
    assert merged.values[0, 0].tolist() == [-10, -21, -12, NODATA]  # no averaging, nothing outside aoi
    assert orbit_map.values.tolist() == [[1, 2, 1, ORBIT_MAP_NODATA]]


def test_pixel_seen_by_no_series_is_nodata():
    merged, orbit_map = fill_composites([comp(NODATA, 1, 1, 1)], [0], AOI)
    assert merged.values[0, 0, 0] == NODATA and orbit_map.values[0, 0] == ORBIT_MAP_NODATA


def test_single_series_returns_its_own_data_with_one_orbit_map_value():
    only = comp(-10, -11, -12, -13)
    merged, orbit_map = fill_composites([only], [0], AOI)
    assert merged.values[0, 0].tolist() == [-10, -11, -12, NODATA]
    assert set(orbit_map.values[AOI.values].tolist()) == {1}
