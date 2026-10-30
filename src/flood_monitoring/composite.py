"""Before and After Composites."""

from __future__ import annotations

import json
import logging

import numpy as np
import rasterio
import xarray as xr

from . import chunks, raster
from .config import EventConfig
from .cube import georeferenced
from .detection import to_db_lazy
from .event import acquisition_dates
from .paths import EventPaths

logger = logging.getLogger(__name__)

NODATA = -9999.0  # in memory, dB float32
STORED_NODATA = -32768  # in the files, int16
SCALE = 0.01  # dB per stored unit: files hold round(dB / SCALE)
BANDS = ("vv", "vh", "vv_minus_vh")


def composite_bands(vv: xr.DataArray, vh: xr.DataArray, nodata: float | None = None) -> xr.DataArray:
    """Composite of one Acquisition: (band, y, x) float32 of VV dB, VH dB and VV minus VH dB.

    *vv* and *vh* are (y, x) linear power. A pixel invalid in either polarization is :data:`NODATA`
    in all three bands. Lazy on dask input.
    """
    vv_db, vh_db = to_db_lazy(vv, nodata), to_db_lazy(vh, nodata)
    out = xr.concat([vv_db, vh_db, vv_db - vh_db], dim="band").assign_coords(band=list(BANDS))
    return out.where(np.isfinite(out).all("band"), NODATA).astype("float32")


def encode(composite: xr.DataArray) -> xr.DataArray:
    """int16 of a dB float32 Composite for the files: :data:`NODATA` becomes :data:`STORED_NODATA`."""
    info = np.iinfo("int16")
    scaled = np.clip(np.round(composite / SCALE), info.min + 1, info.max)  # -32768 is reserved for nodata
    return xr.where(composite == NODATA, STORED_NODATA, scaled).astype("int16")


def decode(stored: xr.DataArray) -> xr.DataArray:
    """dB float32 of an int16 Composite read from a file: :data:`STORED_NODATA` becomes :data:`NODATA`."""
    return xr.where(stored == STORED_NODATA, NODATA, stored.astype("float32") * np.float32(SCALE)).astype("float32")


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


def extract_vv(paths: EventPaths) -> None:
    """Write the VV band (band 1) of each Composite as its own single-band file, unless it exists."""
    for src, dst in ((paths.before_composite, paths.before_vv), (paths.after_composite, paths.after_vv)):
        if dst.exists() or not src.exists():
            continue
        with rasterio.open(src) as ds:
            profile = {**ds.profile, "count": 1}
            with rasterio.open(raster.partial(dst), "w", **profile) as out:
                out.scales, out.offsets = ds.scales[:1], ds.offsets[:1]
                out.set_band_description(1, "VV dB")
                for _, window in ds.block_windows(1):
                    out.write(ds.read(1, window=window), 1, window=window)
        raster.commit([dst])
        print(f"VV: {dst}")


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    targets = {"before": paths.before_composite, "after": paths.after_composite}
    if all(p.exists() for p in targets.values()) and not force:
        print(f"skip: {paths.processed} Composites exist")
        extract_vv(paths)
        return
    for needed, stage in ((paths.summary, "flood-extent"), (paths.filtered_cube, "despeckle")):
        if not needed.exists():
            raise FileNotFoundError(f"{needed} is missing; run the {stage} stage first")
    summary = json.loads(paths.summary.read_text())
    if "peak" not in summary:
        raise FileNotFoundError(f"{paths.summary} has no Peak; run the flood-extent stage first")

    raster.clear([*targets.values(), paths.before_vv, paths.after_vv])  # both are the skip sentinel
    chosen = {"before": summary["before"], "after": summary["peak"]}
    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=28, time_chunk=1)
    with ds, chunks.scheduler(plan.workers, budget):
        missing = [v for v in ("VV", "VH") if v not in ds.data_vars]
        if missing:
            logger.warning(
                "no Composites for %s: the Cube lacks %s, and a Composite needs both VV and VH",
                config.event, " and ".join(missing),
            )
            return
        dates = acquisition_dates(ds["time"].values)
        nodata = ds.attrs.get("nodata")
        vv, vh = georeferenced(ds, "VV"), georeferenced(ds, "VH")
        rasters = []
        for name, day in chosen.items():
            i = dates.index(day)
            comp = composite_bands(vv.isel(time=i, drop=True), vh.isel(time=i, drop=True), nodata)
            rasters.append(
                raster.Raster(
                    encode(comp), targets[name], "int16", vv.rio.crs, STORED_NODATA, "VV dB, VH dB, VV minus VH dB",
                    scale=SCALE,
                )
            )
        raster.write_partial(rasters)
    raster.commit(list(targets.values()))
    extract_vv(paths)
    for name, day in chosen.items():
        print(f"{name} Composite ({day}): {targets[name]}")
