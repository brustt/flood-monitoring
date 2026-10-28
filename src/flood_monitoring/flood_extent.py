"""Water Masks of the Post-event Acquisitions, then the Flood Extent."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from eo_pipe.steps.raster.sieve import SieveStep

from .config import Detection, EventConfig
from .detection import flood_extent, peak, permanent_water, to_db, water
from .event import acquisition_dates
from .paths import EventPaths


def write_mask(mask: np.ndarray, template: xr.DataArray, path: Path) -> None:
    """Write a boolean (y, x) mask as a uint8 GeoTIFF on the grid of *template*."""
    da = xr.DataArray(mask.astype("uint8"), dims=("y", "x"), coords={"y": template["y"], "x": template["x"]})
    da = da.rio.write_crs(template.rio.crs)
    path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(path, dtype="uint8")


def read_mask(path: Path) -> np.ndarray:
    with rioxarray.open_rasterio(path) as da:
        return da.values[0].astype(bool)


def sieve_masks(masks: np.ndarray, template: xr.DataArray, work: Path, size: int, connectedness: int) -> np.ndarray:
    """eo-pipe's sieve step on each (y, x) mask of *masks*; returns the sieved masks in the same order.

    eo-pipe works on files, so the masks go through GeoTIFFs in *work*, which is emptied first
    and removed once the sieved masks are read back.
    """
    shutil.rmtree(work, ignore_errors=True)
    inputs = [work / "raw" / f"{i}.tif" for i in range(len(masks))]
    for mask, path in zip(masks, inputs):
        write_mask(mask, template, path)
    (work / "sieved").mkdir()
    result = SieveStep().execute(inputs, work / "sieved", threshold=size, connectedness=connectedness)
    sieved = np.stack([read_mask(Path(o.path)) for o in result.outputs])
    shutil.rmtree(work)
    return sieved


def load_vv_db(path: Path) -> tuple[list[str], np.ndarray, xr.DataArray]:
    """Dates, VV in dB (time, y, x) and a georeferenced (y, x) template grid of the Filtered Cube at *path*."""
    with xr.open_dataset(path) as cube:
        vv = cube["VV"]
        dates = acquisition_dates(cube["time"].values)
        db = to_db(vv.values, cube.attrs.get("nodata"))
        template = vv.isel(time=0, drop=True).load()
        template = template.rio.write_crs(cube["spatial_ref"].attrs["spatial_ref"])
    return dates, db, template


def pixel_area_km2(template: xr.DataArray) -> float:
    return abs(float(template["x"].diff("x").mean()) * float(template["y"].diff("y").mean())) / 1e6


def extent_summary(
    rules: Detection,
    post_dates: list[str],
    permanent: np.ndarray,
    masks: np.ndarray,
    extent: np.ndarray,
    pixel_km2: float,
) -> dict:
    """Peak, the rules used and the areas in km2, to add to the Event summary."""
    peak_i, areas = peak(masks, permanent)
    return {
        "peak": post_dates[peak_i],
        "rules_extent": {
            "water_db": rules.water_db,
            "permanent_fraction": rules.permanent_fraction,
            "sieve_size": rules.sieve_size,
            "sieve_connectedness": rules.sieve_connectedness,
        },
        "flooded_area_km2": {day: round(a * pixel_km2, 3) for day, a in zip(post_dates, areas)},
        "permanent_water_km2": round(int(permanent.sum()) * pixel_km2, 3),
        "flood_extent_km2": round(int(extent.sum()) * pixel_km2, 3),
    }


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.flood_extent.exists() and not force:
        print(f"skip: {paths.flood_extent} exists")
        return
    if not paths.summary.exists():
        raise FileNotFoundError(f"{paths.summary} is missing; run the event stage first")
    if not paths.filtered_cube.exists():
        raise FileNotFoundError(f"{paths.filtered_cube} is missing; run the despeckle stage first")

    rules = config.detection
    summary = json.loads(paths.summary.read_text())
    dates, db, template = load_vv_db(paths.filtered_cube)
    start = dates.index(summary["event_start"])
    post_dates = dates[start:]

    # Raw masks: Permanent Water first (water in most pre-event dates, thresholded before sieve),
    # then one Water Mask per Post-event Acquisition. All are sieved alike.
    pre_water = np.stack([water(d, rules.water_db) for d in db[:start]])
    raw = np.stack(
        [permanent_water(pre_water, rules.permanent_fraction), *(water(d, rules.water_db) for d in db[start:])]
    )
    sieved = sieve_masks(raw, template, paths.sieve_work, rules.sieve_size, rules.sieve_connectedness)
    permanent, masks = sieved[0], sieved[1:]
    extent = flood_extent(masks, permanent)

    shutil.rmtree(paths.water_masks, ignore_errors=True)  # no stale dates left from a previous run
    for day, mask in zip(post_dates, masks):
        write_mask(mask, template, paths.water_mask(day))
    write_mask(permanent, template, paths.permanent_water)
    write_mask(extent, template, paths.flood_extent)

    summary.update(extent_summary(rules, post_dates, permanent, masks, extent, pixel_area_km2(template)))
    paths.summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Peak {summary['peak']}, Flood Extent {summary['flood_extent_km2']} km2")
