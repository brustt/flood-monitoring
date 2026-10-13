"""Pure array rules of the detection (see docs/detection-rules.md); no files, no eo-pipe."""

from __future__ import annotations

import warnings

import numpy as np


def to_db(power: np.ndarray, nodata: float | None = None) -> np.ndarray:
    """Linear power to dB; nodata, NaN and non-positive values become NaN."""
    power = np.asarray(power, dtype="float32")
    valid = np.isfinite(power) & (power > 0)
    if nodata is not None and not np.isnan(nodata):
        valid &= power != nodata
    return np.where(valid, 10 * np.log10(np.where(valid, power, 1)), np.nan).astype("float32")


def drop_share(db: np.ndarray, k: int, drop_db: float) -> float:
    """Share of pixels whose Drop at step *k* exceeds *drop_db*.

    The Baseline is the per-pixel median of steps ``0..k-1``. Pixels with no valid value
    in the Baseline or at *k* are left out of the share.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels give NaN, handled below
        baseline = np.nanmedian(db[:k], axis=0)
    drop = baseline - db[k]
    valid = np.isfinite(drop)
    if not valid.any():
        return 0.0
    return float((drop[valid] > drop_db).mean())


def event_start(db: np.ndarray, min_baseline: int, drop_db: float, min_share: float) -> tuple[int, list[float]]:
    """Index of the first step whose Drop share exceeds *min_share*, and the share per candidate.

    Candidates start at step *min_baseline* (so the Baseline has that many Acquisitions).
    Raises ValueError if the Series is too short or no step qualifies.
    """
    if db.shape[0] <= min_baseline:
        raise ValueError(f"need more than {min_baseline} Acquisitions to detect the Event Start, got {db.shape[0]}")
    shares: list[float] = []
    for k in range(min_baseline, db.shape[0]):
        share = drop_share(db, k, drop_db)
        shares.append(share)
        if share > min_share:
            return k, shares
    raise ValueError(f"no Event Start: Drop share never exceeded {min_share:.1%} (max {max(shares):.2%})")


def water(db: np.ndarray, threshold_db: float) -> np.ndarray:
    """Boolean water: VV below *threshold_db* (NaN is not water)."""
    return db < threshold_db


def permanent_water(pre_water: np.ndarray, fraction: float) -> np.ndarray:
    """Pixels that are water in more than *fraction* of the pre-event steps.

    *pre_water* is a boolean (time, y, x) array of the raw pre-event Water Masks.
    """
    if pre_water.shape[0] == 0:
        raise ValueError("no pre-event Acquisitions to derive Permanent Water from")
    return pre_water.mean(axis=0) > fraction


def flood_extent(water_masks: np.ndarray, permanent: np.ndarray) -> np.ndarray:
    """Union of the post-event Water Masks, minus Permanent Water."""
    return water_masks.any(axis=0) & ~permanent


def peak(water_masks: np.ndarray, permanent: np.ndarray) -> tuple[int, list[int]]:
    """Index of the step with the most water pixels outside Permanent Water, and every step's count."""
    areas = [int((m & ~permanent).sum()) for m in water_masks]
    return int(np.argmax(areas)), areas
