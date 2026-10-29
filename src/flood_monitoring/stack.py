"""Stack the downloaded Subsets into the Cube."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from eo_pipe.pipeline import MergeBatch, PipelineComposition

from . import chunks
from .config import EventConfig, Series
from .cube import check_cube
from .paths import EventPaths


def subsets(raw: Path, series: Series) -> list[Path]:
    """Raw Subsets of the Series in its date range (bounds included) and assets, in a stable order."""
    found = (
        tif
        for tif in raw.glob(f"{series.collection}/*/*/*.tif")
        if series.start <= date.fromisoformat(tif.parent.parent.name) <= series.end
        and any(f"_{asset}_sub-" in tif.name for asset in series.assets)
    )
    return sorted(found)


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.cube.exists() and not force:
        print(f"skip: {paths.cube} exists")
        return
    inputs = subsets(paths.raw, config.series)
    if not inputs:
        raise FileNotFoundError(f"no Subsets in {paths.raw} for {config.series.start} to {config.series.end}; run the download stage first")

    # Native grid, no align_to_tile: the Subsets of one orbit already share a lattice.
    # A pass is split into consecutive frames that need not touch the other dates' frames,
    # so pad to the union and merge each calendar day into one step.
    work = paths.interim / "_stack"
    shutil.rmtree(work, ignore_errors=True)
    ctx = (
        PipelineComposition(workspace=work)
        .add_step(
            "stack_tiles", MergeBatch(), group=config.event, union=True, time_bin="day",
            memory_budget_mb=config.processing.memory_budget_mb,
        )
        .run(inputs, save_intermediate=True)
    )
    built = ctx.inputs[0]
    shutil.move(built, paths.cube)
    shutil.rmtree(work, ignore_errors=True)

    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.cube, budget, bytes_per_date=10)
    with ds, chunks.scheduler(plan.workers, budget):
        acquisitions = len({p.parent.parent.name for p in inputs})
        check_cube(ds, nodata=ds.attrs.get("nodata"), expected=acquisitions)
        print(f"cube: {paths.cube} ({ds.sizes['time']} Acquisitions, variables {list(ds.data_vars)})")
