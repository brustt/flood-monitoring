"""Before and After Composites."""

from __future__ import annotations

import json

import numpy as np
import xarray as xr

from . import chunks, raster
from .config import EventConfig
from .cube import georeferenced
from .detection import to_db_lazy
from .event import acquisition_dates
from .paths import EventPaths

NODATA = -9999.0
BANDS = ("vv", "vh", "vv_minus_vh")


def composite_bands(vv: xr.DataArray, vh: xr.DataArray, nodata: float | None = None) -> xr.DataArray:
    """Composite of one Acquisition: (band, y, x) float32 of VV dB, VH dB and VV minus VH dB.

    *vv* and *vh* are (y, x) linear power. A pixel invalid in either polarization is :data:`NODATA`
    in all three bands. Lazy on dask input.
    """
    vv_db, vh_db = to_db_lazy(vv, nodata), to_db_lazy(vh, nodata)
    out = xr.concat([vv_db, vh_db, vv_db - vh_db], dim="band").assign_coords(band=list(BANDS))
    return out.where(np.isfinite(out).all("band"), NODATA).astype("float32")


def render_rgb(composite: np.ndarray, ranges: dict[str, tuple[float, float]]) -> np.ndarray:
    """8-bit (y, x, 3) picture: each band stretched from its (low, high) range, nodata black."""
    channels = []
    for band, data in zip(BANDS, composite):
        low, high = ranges[band]
        if not high > low:
            raise ValueError(f"range of {band} must have high > low, got ({low}, {high})")
        scaled = np.clip((data - low) / (high - low), 0, 1) * 255
        channels.append(np.where(data == NODATA, 0, np.round(scaled)))
    return np.stack(channels, axis=-1).astype("uint8")


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    targets = {"before": paths.before_composite, "after": paths.after_composite}
    if all(p.exists() for p in targets.values()) and not force:
        print(f"skip: {paths.processed} Composites exist")
        return
    for needed, stage in ((paths.summary, "flood-extent"), (paths.filtered_cube, "despeckle")):
        if not needed.exists():
            raise FileNotFoundError(f"{needed} is missing; run the {stage} stage first")
    summary = json.loads(paths.summary.read_text())
    if "peak" not in summary:
        raise FileNotFoundError(f"{paths.summary} has no Peak; run the flood-extent stage first")

    raster.clear(list(targets.values()))  # both are the skip sentinel
    chosen = {"before": summary["before"], "after": summary["peak"]}
    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=28, time_chunk=1)
    with ds, chunks.scheduler(plan.workers, budget):
        dates = acquisition_dates(ds["time"].values)
        nodata = ds.attrs.get("nodata")
        vv, vh = georeferenced(ds, "VV"), georeferenced(ds, "VH")
        rasters = []
        for name, day in chosen.items():
            i = dates.index(day)
            comp = composite_bands(vv.isel(time=i, drop=True), vh.isel(time=i, drop=True), nodata)
            rasters.append(
                raster.Raster(comp, targets[name], "float32", vv.rio.crs, NODATA, "VV dB, VH dB, VV minus VH dB")
            )
        raster.write_partial(rasters)
    raster.commit(list(targets.values()))
    for name, day in chosen.items():
        print(f"{name} Composite ({day}): {targets[name]}")
