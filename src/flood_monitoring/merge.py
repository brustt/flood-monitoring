"""Merge core: per-Series results on the Event grid into global Layers. Pure functions on arrays, no I/O.

All arrays share the Event grid. ``aoi`` is the boolean (y, x) mask of the Area of Interest.
"""

from __future__ import annotations

import numpy as np

from .composite import NODATA

ORBIT_MAP_NODATA = 0  # Orbit Map values are Series ranks, 1 for the Main Series


def valid_composite(composite: np.ndarray) -> np.ndarray:
    """Boolean (y, x) mask of the pixels holding data in a (3, y, x) Composite."""
    return (composite != NODATA).all(axis=0) & np.isfinite(composite).all(axis=0)


def coverage(valid: list[np.ndarray], aoi: np.ndarray) -> list[float]:
    """Share of the Area of Interest covered by each Series' valid pixels."""
    total = int(aoi.sum())
    if total == 0:
        raise ValueError("the Area of Interest holds no pixel on the Event grid")
    return [int((v & aoi).sum()) / total for v in valid]


def rank_series(valid: list[np.ndarray], aoi: np.ndarray) -> list[int]:
    """Indices of the Series, Main Series first: most coverage first, ties in input (config) order."""
    shares = coverage(valid, aoi)
    return sorted(range(len(valid)), key=lambda i: (-shares[i], i))


def merge_flood_extent(extents: list[np.ndarray], aoi: np.ndarray) -> np.ndarray:
    """Logical OR of the per-Series Flood Extents, kept inside the Area of Interest.

    Sieving the result is left to the caller, so that patches cut at an orbit edge are judged whole.
    """
    merged = np.logical_or.reduce(extents)
    return merged & aoi


def fill_composites(
    composites: list[np.ndarray], order: list[int], aoi: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Priority fill, never an average: each pixel comes from the first Series of *order* with data there.

    Returns the merged (3, y, x) Composite and the uint8 Orbit Map holding the rank (1-based position
    in *order*) of the Series each pixel came from. Pixels outside the Area of Interest, or seen by no
    Series, are :data:`~flood_monitoring.composite.NODATA` and :data:`ORBIT_MAP_NODATA`.
    """
    merged = np.full_like(composites[0], NODATA)
    orbit_map = np.full(aoi.shape, ORBIT_MAP_NODATA, dtype="uint8")
    open_ = aoi.copy()
    for rank, i in enumerate(order, start=1):
        take = open_ & valid_composite(composites[i])
        merged[:, take] = composites[i][:, take]
        orbit_map[take] = rank
        open_ &= ~take
    return merged, orbit_map
