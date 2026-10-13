"""Water Masks of the Post-event Acquisitions, then the Flood Extent."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from eo_pipe.steps.raster.sieve import SieveStep

from .config import EventConfig
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


def sieve(masks: list[Path], work: Path, size: int, connectedness: int) -> list[Path]:
    """eo-pipe's sieve step on each mask file; returns the sieved files in the same order."""
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    result = SieveStep().execute(masks, work, threshold=size, connectedness=connectedness)
    return [Path(o.path) for o in result.outputs]


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
    with xr.open_dataset(paths.filtered_cube) as cube:
        vv = cube["VV"]
        dates = acquisition_dates(cube["time"].values)
        db = to_db(vv.values, cube.attrs.get("nodata"))
        template = vv.isel(time=0, drop=True).load()
        template = template.rio.write_crs(cube["spatial_ref"].attrs["spatial_ref"])
    start = dates.index(summary["event_start"])
    post = list(range(start, len(dates)))

    work = paths.processed / "_sieve"
    raw = work / "raw"
    # Permanent Water: water in most pre-event dates, before sieve.
    pre_water = np.stack([water(db[i], rules.water_db) for i in range(start)])
    write_mask(permanent_water(pre_water, rules.permanent_fraction), template, raw / "permanent-water.tif")
    for i in post:
        write_mask(water(db[i], rules.water_db), template, raw / f"{dates[i]}.tif")

    inputs = [raw / "permanent-water.tif", *(raw / f"{dates[i]}.tif" for i in post)]
    sieved = sieve(inputs, work / "sieved", rules.sieve_size, rules.sieve_connectedness)
    permanent = read_mask(sieved[0])
    masks = np.stack([read_mask(p) for p in sieved[1:]])

    shutil.rmtree(paths.water_masks, ignore_errors=True)
    for i, mask in zip(post, masks):
        write_mask(mask, template, paths.water_masks / f"{dates[i]}.tif")
    write_mask(permanent, template, paths.permanent_water)
    extent = flood_extent(masks, permanent)
    write_mask(extent, template, paths.flood_extent)

    peak_i, areas = peak(masks, permanent)
    pixel_km2 = abs(float(template["x"].diff("x").mean()) * float(template["y"].diff("y").mean())) / 1e6
    summary.update(
        {
            "peak": dates[post[peak_i]],
            "rules_extent": {
                "water_db": rules.water_db,
                "permanent_fraction": rules.permanent_fraction,
                "sieve_size": rules.sieve_size,
                "sieve_connectedness": rules.sieve_connectedness,
            },
            "flooded_area_km2": {dates[i]: round(a * pixel_km2, 3) for i, a in zip(post, areas)},
            "permanent_water_km2": round(int(permanent.sum()) * pixel_km2, 3),
            "flood_extent_km2": round(int(extent.sum()) * pixel_km2, 3),
        }
    )
    paths.summary.write_text(json.dumps(summary, indent=2) + "\n")
    shutil.rmtree(work, ignore_errors=True)
    print(f"Peak {dates[post[peak_i]]}, Flood Extent {summary['flood_extent_km2']} km2")
