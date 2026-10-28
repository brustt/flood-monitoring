"""Water Masks of the Post-event Acquisitions, then the Flood Extent."""

from __future__ import annotations

import json
import os
import shutil

import xarray as xr

from . import chunks, raster
from .config import Detection, EventConfig
from .cube import georeferenced
from .detection import flood_extent, flooded_areas, peak, permanent_water, to_db_lazy, water
from .event import acquisition_dates
from .paths import EventPaths


def pixel_area_km2(template: xr.DataArray) -> float:
    return abs(float(template["x"].diff("x").mean()) * float(template["y"].diff("y").mean())) / 1e6


def extent_summary(
    rules: Detection,
    post_dates: list[str],
    areas: list[int],
    n_permanent: int,
    n_extent: int,
    pixel_km2: float,
) -> dict:
    """Peak, the rules used and the areas in km2, to add to the Event summary.

    *areas* are the water pixels outside Permanent Water per Post-event Acquisition; *n_permanent*
    and *n_extent* the pixels of Permanent Water and of the Flood Extent.
    """
    return {
        "peak": post_dates[peak(areas)],
        "rules_extent": {
            "water_db": rules.water_db,
            "permanent_fraction": rules.permanent_fraction,
            "sieve_size": rules.sieve_size,
            "sieve_connectedness": rules.sieve_connectedness,
        },
        "flooded_area_km2": {day: round(a * pixel_km2, 3) for day, a in zip(post_dates, areas)},
        "permanent_water_km2": round(n_permanent * pixel_km2, 3),
        "flood_extent_km2": round(n_extent * pixel_km2, 3),
    }


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.flood_extent.exists() and not force:
        print(f"skip: {paths.flood_extent} exists")
        return
    if not paths.summary.exists():
        raise FileNotFoundError(f"{paths.summary} is missing; run the event stage first")
    if not paths.filtered_cube.exists():
        raise FileNotFoundError(f"{paths.filtered_cube} is missing; run the despeckle stage first")

    # The skip sentinel first, then any leftovers (stale dates, partial files) of an earlier run.
    raster.clear([paths.flood_extent, paths.permanent_water], [paths.water_masks, paths.sieve_work])
    rules = config.detection
    summary = json.loads(paths.summary.read_text())
    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=9)
    with ds, chunks.scheduler(plan.workers, budget):
        dates = acquisition_dates(ds["time"].values)
        start = dates.index(summary["event_start"])
        post_dates = dates[start:]
        vv = georeferenced(ds, "VV")
        crs = vv.rio.crs
        wet = water(to_db_lazy(vv, ds.attrs.get("nodata")), rules.water_db)
        template = wet.isel(time=0, drop=True)

        # Raw masks: Permanent Water first (water in most pre-event dates, thresholded before sieve),
        # then one Water Mask per Post-event Acquisition. All are sieved alike, each as a whole.
        raw = [
            permanent_water(wet.isel(time=slice(None, start)), rules.permanent_fraction),
            *(wet.isel(time=i, drop=True) for i in range(start, len(dates))),
        ]
        files = raster.sieve(raw, crs, paths.sieve_work, rules.sieve_size, rules.sieve_connectedness)
        sieved = [raster.open_mask(f, template, plan.side) for f in files]
        try:
            permanent = sieved[0]
            masks = xr.concat(sieved[1:], dim="time").assign_coords(time=ds["time"].values[start:])
            extent = flood_extent(masks, permanent)
            areas, n_permanent, n_extent = raster.write_partial(
                [raster.Raster(extent, paths.flood_extent, "uint8", crs)],
                flooded_areas(masks, permanent),
                permanent.sum(),
                extent.sum(),
            )
        finally:
            for mask in sieved:
                mask.close()

    # The sieved files become the Water Masks and Permanent Water.
    paths.water_masks.mkdir(parents=True)
    for day, f in zip(post_dates, files[1:]):
        os.replace(f, paths.water_mask(day))
    os.replace(files[0], paths.permanent_water)
    shutil.rmtree(paths.sieve_work)

    summary.update(
        extent_summary(rules, post_dates, [int(a) for a in areas], int(n_permanent), int(n_extent),
                       pixel_area_km2(template))
    )
    raster.write_json(paths.summary, summary)
    raster.commit([paths.flood_extent])  # last: the skip sentinel of the stage
    print(f"Peak {summary['peak']}, Flood Extent {summary['flood_extent_km2']} km2")
