"""Detect the Event Start from the VV Drop."""

from __future__ import annotations

import numpy as np

from . import aoi, chunks, raster
from .config import EventConfig
from .cube import georeferenced
from .detection import event_start, to_db_lazy
from .paths import EventPaths


def acquisition_dates(times: np.ndarray) -> list[str]:
    return [str(t.astype("datetime64[D]")) for t in times]


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.summary.exists() and not force:
        print(f"skip: {paths.summary} exists")
        return
    if not paths.filtered_cube.exists():
        raise FileNotFoundError(f"{paths.filtered_cube} is missing; run the despeckle stage first")

    raster.clear([paths.summary])  # the skip sentinel first: a killed forced run leaves no stale summary
    rules = config.detection
    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=40)
    with ds, chunks.scheduler(plan.workers, budget):
        dates = acquisition_dates(ds["time"].values)
        db = to_db_lazy(georeferenced(ds, "VV"), ds.attrs.get("nodata"))
        # the share is of the polygon's pixels, not the bounding box's
        inside = aoi.mask_like(config.aoi_polygon, db) if config.aoi_polygon is not None else None
        try:
            k, shares = event_start(db, rules.min_baseline, rules.drop_db, rules.event_share, inside)
        except ValueError as exc:
            orbit = f"relative orbit {paths.orbit}: " if paths.orbit is not None else ""
            raise ValueError(f"{orbit}{exc}") from exc

    summary = {
        "event": config.event,
        "event_start": dates[k],
        "before": dates[k - 1],
        "acquisitions": len(dates),
        "rules": {
            "min_baseline": rules.min_baseline,
            "drop_db": rules.drop_db,
            "event_share": rules.event_share,
        },
        "drop_share": dict(zip(dates[rules.min_baseline : k + 1], shares)),
    }
    paths.processed.mkdir(parents=True, exist_ok=True)
    raster.write_json(paths.summary, summary)
    print(f"Event Start {dates[k]} (Before {dates[k - 1]}), share {shares[-1]:.2%}")
