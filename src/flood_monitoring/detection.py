"""Pure array rules of the detection (see docs/detection-rules.md); no files, no eo-pipe.

The rules work on (lazy) DataArrays; masks are boolean. :func:`to_db` and :func:`drop_counts`
are the numpy kernels applied per chunk.
"""

from __future__ import annotations

import dask.array
import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from dask import delayed
from dask.delayed import Delayed

CANDIDATE_BATCH = 8  # Event Start candidates per read of the Filtered Cube


def to_db(power: np.ndarray, nodata: float | None = None) -> np.ndarray:
    """Linear power to dB; nodata, NaN and non-positive values become NaN."""
    power = np.asarray(power, dtype="float32")
    valid = np.isfinite(power) & (power > 0)
    if nodata is not None and not np.isnan(nodata):
        valid &= power != nodata
    return np.where(valid, 10 * np.log10(np.where(valid, power, 1)), np.nan).astype("float32")


def to_db_lazy(power: xr.DataArray, nodata: float | None = None) -> xr.DataArray:
    """:func:`to_db` per chunk; variable attributes dropped, coords and CRS kept."""
    db = xr.apply_ufunc(
        to_db, power, kwargs={"nodata": nodata}, dask="parallelized", output_dtypes=[np.float32], keep_attrs=False
    )
    # apply_ufunc drops the encoding that links the variable to its CRS
    return db.rio.write_crs(power.rio.crs) if power.rio.crs is not None else db


def _check_length(n: int, min_baseline: int) -> None:
    if n <= min_baseline:
        raise ValueError(f"need more than {min_baseline} Acquisitions to detect the Event Start, got {n}")


def drop_counts(
    db: np.ndarray, candidates: range, min_baseline: int, drop_db: float, inside: np.ndarray | None = None
) -> np.ndarray:
    """Per candidate step k of a (time, y, x) block: pixels with a valid Drop, and those whose Drop exceeds *drop_db*.

    The Baseline is the per-pixel median of steps ``0..k-1``. Pixels with no valid value in the
    Baseline or at k are left out, and so are pixels outside *inside* (the Area of Interest mask).
    Returns a (len(candidates), 2) int64 array.
    """
    _check_length(db.shape[0], min_baseline)
    out = np.zeros((len(candidates), 2), dtype="int64")
    for row, k in enumerate(candidates):
        prior = db[:k]
        has = np.isfinite(prior).any(axis=0)  # all-NaN pixels skipped: no RuntimeWarning
        baseline = np.full(db.shape[1:], np.nan, dtype=db.dtype)
        baseline[has] = np.nanmedian(prior[:, has], axis=0)
        drop = baseline - db[k]
        valid = np.isfinite(drop)
        if inside is not None:
            valid &= inside
        out[row] = int(valid.sum()), int((drop[valid] > drop_db).sum())
    return out


def series_drop_counts(
    db: xr.DataArray, candidates: range, min_baseline: int, drop_db: float, inside: xr.DataArray | None = None
) -> Delayed:
    """:func:`drop_counts` of every spatial chunk of a (time, y, x) *db*, summed (delayed)."""
    _check_length(db.sizes["time"], min_baseline)
    db = db.transpose("time", "y", "x").chunk({"time": -1})
    blocks = db.data.to_delayed()
    masks = None
    if inside is not None:
        inside = inside.chunk({"y": db.chunksizes["y"], "x": db.chunksizes["x"]})
        masks = inside.data.to_delayed()
    parts = [
        delayed(drop_counts)(blocks[0, i, j], candidates, min_baseline, drop_db, None if masks is None else masks[i, j])
        for i, j in np.ndindex(blocks.shape[1:])
    ]
    return delayed(sum)(parts)


def event_start(
    db: xr.DataArray,
    min_baseline: int,
    drop_db: float,
    min_share: float,
    inside: xr.DataArray | None = None,
    batch: int = CANDIDATE_BATCH,
) -> tuple[int, list[float]]:
    """Index of the first step whose Drop share exceeds *min_share*, and the share per candidate up to it.

    Candidates start at step *min_baseline* (so the Baseline has that many Acquisitions) and are
    evaluated *batch* at a time, one read of *db* per batch; evaluation stops after the batch
    holding the Event Start. Raises ValueError if the Series is too short or no step qualifies.
    """
    n = db.sizes["time"]
    _check_length(n, min_baseline)
    if not isinstance(db.data, dask.array.Array):
        db = db.chunk()
    shares: list[float] = []
    for first in range(min_baseline, n, batch):
        candidates = range(first, min(first + batch, n))
        counts = series_drop_counts(db, candidates, min_baseline, drop_db, inside).compute()
        for k, (n_valid, n_drop) in zip(candidates, counts):
            share = float(n_drop / n_valid) if n_valid else 0.0
            shares.append(share)
            if share > min_share:
                return k, shares
    raise ValueError(f"no Event Start: Drop share never exceeded {min_share:.1%} (max {max(shares):.2%})")


def water(db: xr.DataArray, threshold_db: float) -> xr.DataArray:
    """Boolean water: VV below *threshold_db* (NaN is not water)."""
    return db < threshold_db


def permanent_water(pre_water: xr.DataArray, fraction: float) -> xr.DataArray:
    """Pixels that are water in more than *fraction* of the pre-event steps.

    *pre_water* is a boolean (time, y, x) array of the raw pre-event Water Masks.
    """
    if pre_water.sizes["time"] == 0:
        raise ValueError("no pre-event Acquisitions to derive Permanent Water from")
    return pre_water.mean("time") > fraction


def flood_extent(water_masks: xr.DataArray, permanent: xr.DataArray) -> xr.DataArray:
    """Union of the post-event Water Masks, minus Permanent Water."""
    return water_masks.any("time") & ~permanent


def flooded_areas(water_masks: xr.DataArray, permanent: xr.DataArray) -> xr.DataArray:
    """Water pixels outside Permanent Water, per step."""
    return (water_masks & ~permanent).sum(("y", "x"))


def peak(areas: list[int]) -> int:
    """Index of the step with the most water pixels outside Permanent Water."""
    return int(np.argmax(areas))
