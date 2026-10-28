"""Merge core: per-Series results on the Event grid into global Layers. Pure functions on (lazy) DataArrays, no I/O.

All arrays share the Event grid. ``aoi`` is the boolean (y, x) mask of the Area of Interest.
"""

from __future__ import annotations

import functools
import operator

import numpy as np
import xarray as xr

from .composite import NODATA

ORBIT_MAP_NODATA = 0  # Orbit Map values are Series ranks, 1 for the Main Series


def valid_composite(composite: xr.DataArray) -> xr.DataArray:
    """Boolean (y, x) mask of the pixels holding data in a (band, y, x) Composite."""
    return (composite != NODATA).all("band") & np.isfinite(composite).all("band")


def coverage(counts: list[int], total: int) -> list[float]:
    """Share of the Area of Interest (*total* pixels) covered by each Series (*counts* valid pixels inside it)."""
    if total == 0:
        raise ValueError("the Area of Interest holds no pixel on the Event grid")
    return [c / total for c in counts]


def rank_series(shares: list[float]) -> list[int]:
    """Indices of the Series, Main Series first: most coverage first, ties in input (config) order."""
    return sorted(range(len(shares)), key=lambda i: (-shares[i], i))


def merge_flood_extent(extents: list[xr.DataArray], aoi: xr.DataArray) -> xr.DataArray:
    """Logical OR of the per-Series Flood Extents, kept inside the Area of Interest.

    Sieving the result is left to the caller, so that patches cut at an orbit edge are judged whole.
    """
    return functools.reduce(operator.or_, extents) & aoi


def fill_composites(
    composites: list[xr.DataArray], order: list[int], aoi: xr.DataArray
) -> tuple[xr.DataArray, xr.DataArray]:
    """Priority fill, never an average: each pixel comes from the first Series of *order* with data there.

    Returns the merged (band, y, x) float32 Composite and the uint8 Orbit Map holding the rank (1-based
    position in *order*) of the Series each pixel came from. Pixels outside the Area of Interest, or seen
    by no Series, are :data:`~flood_monitoring.composite.NODATA` and :data:`ORBIT_MAP_NODATA`.
    """
    merged = xr.full_like(composites[0], NODATA, dtype="float32")
    orbit_map = xr.full_like(aoi, ORBIT_MAP_NODATA, dtype="uint8")
    open_ = aoi
    for rank, i in enumerate(order, start=1):
        take = open_ & valid_composite(composites[i])
        # DataArray.where keeps the caller's dims: merged stays (band, y, x)
        merged = merged.where(~take, composites[i])
        orbit_map = orbit_map.where(~take, np.uint8(rank))
        open_ = open_ & ~take
    return merged.astype("float32"), orbit_map.astype("uint8")
