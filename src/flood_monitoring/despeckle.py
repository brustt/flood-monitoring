"""Reduce speckle in the Cube (Lee, then Quegan) to give the Filtered Cube."""

from __future__ import annotations

import shutil

import xarray as xr
from eo_pipe.pipeline import ParallelBatch, PipelineComposition

from . import chunks
from .config import EventConfig
from .cube import check_cube
from .paths import EventPaths


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.filtered_cube.exists() and not force:
        print(f"skip: {paths.filtered_cube} exists")
        return
    if not paths.cube.exists():
        raise FileNotFoundError(f"{paths.cube} is missing; run the stack stage first")

    settings = config.despeckle
    budget = config.processing.memory_budget_mb
    work = paths.interim / "_despeckle"
    shutil.rmtree(work, ignore_errors=True)
    # Inputs stay in linear power: the Cube is never converted to dB before this point.
    ctx = (
        PipelineComposition(workspace=work)
        .add_step(
            "lee_filter", ParallelBatch(), window_size=settings.lee_window, enl=settings.enl, memory_budget_mb=budget
        )
        .add_step("quegan_filter", ParallelBatch(), window_size=settings.quegan_window, memory_budget_mb=budget)
        .run([paths.cube], save_intermediate=True)
    )
    shutil.move(ctx.inputs[0], paths.filtered_cube)
    shutil.rmtree(work, ignore_errors=True)

    with xr.open_dataset(paths.cube) as cube:
        expected = cube.sizes["time"]
    filtered, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=10)
    with filtered, chunks.scheduler(plan.workers, budget):
        check_cube(filtered, nodata=filtered.attrs.get("nodata"), expected=expected)
        print(f"filtered cube: {paths.filtered_cube} ({filtered.sizes['time']} Acquisitions)")
