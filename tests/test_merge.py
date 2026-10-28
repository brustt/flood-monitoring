import numpy as np

from flood_monitoring.composite import NODATA
from flood_monitoring.merge import (
    ORBIT_MAP_NODATA,
    fill_composites,
    merge_flood_extent,
    rank_series,
)

AOI = np.array([[1, 1, 1, 0]], dtype=bool)


def comp(*values):
    """(3, 1, 4) Composite: each band holds *values* (NODATA marks no data)."""
    return np.broadcast_to(np.array(values, dtype="float32"), (3, 1, len(values))).copy()


def test_most_valid_pixels_in_aoi_ranks_first():
    few = np.array([[1, 0, 0, 0]], dtype=bool)
    many = np.array([[1, 1, 0, 0]], dtype=bool)
    assert rank_series([few, many], AOI) == [1, 0]


def test_ties_follow_config_order():
    same = np.array([[1, 1, 0, 0]], dtype=bool)
    assert rank_series([same, same.copy()], AOI) == [0, 1]


def test_pixels_outside_aoi_do_not_count():
    inside = np.array([[1, 0, 0, 0]], dtype=bool)
    outside = np.array([[0, 0, 0, 1]], dtype=bool)
    wide = np.array([[0, 0, 0, 1]], dtype=bool) | inside
    assert rank_series([outside, inside], AOI) == [1, 0]
    assert rank_series([inside, wide], AOI) == [0, 1]


def test_flood_extent_is_flooded_where_any_series_is_flooded_inside_aoi():
    a = np.array([[1, 0, 0, 1]], dtype=bool)
    b = np.array([[0, 1, 0, 1]], dtype=bool)
    assert merge_flood_extent([a, b], AOI).tolist() == [[True, True, False, False]]


def test_composite_fill_takes_main_series_then_next_where_it_has_no_data():
    main = comp(-10, NODATA, -12, -13)
    other = comp(-20, -21, NODATA, -23)
    merged, orbit_map = fill_composites([other, main], [1, 0], AOI)
    assert merged[0, 0].tolist() == [-10, -21, -12, NODATA]  # no averaging, nothing outside aoi
    assert orbit_map.tolist() == [[1, 2, 1, ORBIT_MAP_NODATA]]


def test_pixel_seen_by_no_series_is_nodata():
    merged, orbit_map = fill_composites([comp(NODATA, 1, 1, 1)], [0], AOI)
    assert merged[0, 0, 0] == NODATA and orbit_map[0, 0] == ORBIT_MAP_NODATA


def test_single_series_returns_its_own_data_with_one_orbit_map_value():
    only = comp(-10, -11, -12, -13)
    merged, orbit_map = fill_composites([only], [0], AOI)
    assert merged[0, 0].tolist() == [-10, -11, -12, NODATA]
    assert set(orbit_map[AOI].tolist()) == {1}
