"""Pure checks on a Cube (or Filtered Cube), independent of how it was built."""

from __future__ import annotations

import numpy as np
import xarray as xr


def check_cube(ds: xr.Dataset, nodata: float | None, expected: int | None = None) -> None:
    """Raise if the time axis is not one step per Acquisition, in order, all with data.

    *expected* is the number of Acquisitions; every variable of every step must hold at
    least one pixel that is neither *nodata* nor NaN.
    """
    times = ds["time"].values
    if expected is not None and len(times) != expected:
        raise ValueError(f"cube has {len(times)} time steps, expected {expected}")
    if np.any(np.diff(times) <= np.timedelta64(0, "ns")):
        raise ValueError("cube time axis is not in increasing order")
    for name, var in ds.data_vars.items():
        valid = ~np.isnan(var)
        if nodata is not None and not np.isnan(nodata):
            valid &= var != nodata
        has_data = valid.any(dim=[d for d in var.dims if d != "time"])
        for i in np.flatnonzero(~has_data.values):
            when = np.datetime_as_string(times[i], unit="s")
            raise ValueError(f"cube step {when}: variable {name} has no valid pixel")
