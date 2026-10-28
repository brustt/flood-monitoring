"""Before and After Composites."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr

from .config import EventConfig
from .detection import to_db
from .event import acquisition_dates
from .paths import EventPaths

NODATA = -9999.0
BANDS = ("vv", "vh", "vv_minus_vh")


def composite_bands(vv: np.ndarray, vh: np.ndarray, nodata: float | None = None) -> np.ndarray:
    """Composite of one Acquisition: (3, y, x) float32 of VV dB, VH dB and VV minus VH dB.

    *vv* and *vh* are linear power. A pixel invalid in either polarization is :data:`NODATA`
    in all three bands.
    """
    vv_db, vh_db = to_db(vv, nodata), to_db(vh, nodata)
    out = np.stack([vv_db, vh_db, vv_db - vh_db])
    return np.where(np.isfinite(out).all(axis=0), out, NODATA).astype("float32")


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


def write_composite(composite: np.ndarray, template: xr.DataArray, path: Path) -> None:
    """Write a (3, y, x) Composite as a float32 GeoTIFF on the grid of *template*."""
    da = xr.DataArray(
        composite,
        dims=("band", "y", "x"),
        coords={"band": list(BANDS), "y": template["y"], "x": template["x"]},
    )
    da = da.rio.write_crs(template.rio.crs).rio.write_nodata(NODATA)
    da.attrs["long_name"] = "VV dB, VH dB, VV minus VH dB"
    path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(path, dtype="float32")


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

    chosen = {"before": summary["before"], "after": summary["peak"]}
    with xr.open_dataset(paths.filtered_cube) as cube:
        dates = acquisition_dates(cube["time"].values)
        nodata = cube.attrs.get("nodata")
        template = cube["VV"].isel(time=0, drop=True).load()
        template = template.rio.write_crs(cube["spatial_ref"].attrs["spatial_ref"])
        for name, day in chosen.items():
            i = dates.index(day)
            comp = composite_bands(cube["VV"].isel(time=i).values, cube["VH"].isel(time=i).values, nodata)
            write_composite(comp, template, targets[name])
            print(f"{name} Composite ({day}): {targets[name]}")
