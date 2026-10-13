import numpy as np
import pytest
import xarray as xr

from flood_monitoring.cube import check_cube


def make_cube(times, fill=0.1):
    times = np.array(times, dtype="datetime64[ns]")
    shape = (len(times), 4, 5)
    data = {v: (("time", "y", "x"), np.full(shape, fill, dtype="float32")) for v in ("VV", "VH")}
    return xr.Dataset(data, coords={"time": times})


DATES = ["2026-01-01T06:00", "2026-01-07T06:00", "2026-01-13T06:00"]
NODATA = -32768.0


def test_valid_cube_passes():
    check_cube(make_cube(DATES), nodata=NODATA, expected=3)


def test_wrong_number_of_steps():
    with pytest.raises(ValueError, match="2 time steps, expected 3"):
        check_cube(make_cube(DATES[:2]), nodata=NODATA, expected=3)


def test_unordered_time_axis():
    with pytest.raises(ValueError, match="not in increasing order"):
        check_cube(make_cube([DATES[1], DATES[0], DATES[2]]), nodata=NODATA)


def test_all_nodata_step():
    cube = make_cube(DATES)
    cube["VV"][1] = NODATA
    with pytest.raises(ValueError, match="2026-01-07.*VV.*no valid pixel"):
        check_cube(cube, nodata=NODATA)


def test_all_nan_step():
    cube = make_cube(DATES)
    cube["VH"][2] = np.nan
    with pytest.raises(ValueError, match="2026-01-13.*VH.*no valid pixel"):
        check_cube(cube, nodata=NODATA)


def test_partial_nodata_is_fine():
    cube = make_cube(DATES)
    cube["VV"][1, 0, 0] = NODATA
    check_cube(cube, nodata=NODATA)
