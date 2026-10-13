"""Detect the Event Start from the VV Drop."""

from __future__ import annotations

import json

import numpy as np
import xarray as xr

from .config import EventConfig
from .detection import event_start, to_db
from .paths import EventPaths


def acquisition_dates(times: np.ndarray) -> list[str]:
    return [str(t.astype("datetime64[D]")) for t in times]


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.summary.exists() and not force:
        print(f"skip: {paths.summary} exists")
        return
    if not paths.filtered_cube.exists():
        raise FileNotFoundError(f"{paths.filtered_cube} is missing; run the despeckle stage first")

    rules = config.detection
    with xr.open_dataset(paths.filtered_cube) as cube:
        dates = acquisition_dates(cube["time"].values)
        db = to_db(cube["VV"].values, cube.attrs.get("nodata"))
    k, shares = event_start(db, rules.min_baseline, rules.drop_db, rules.event_share)

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
    paths.summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Event Start {dates[k]} (Before {dates[k - 1]}), share {shares[-1]:.2%}")
