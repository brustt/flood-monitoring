"""Stack the downloaded Subsets into the Cube."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
import xarray as xr
from eo_pipe.io.memory import workers_for
from eo_pipe.pipeline import MergeBatch, PipelineComposition
from rasterio.transform import Affine

from . import aoi, chunks
from .config import EventConfig, Series
from .cube import check_cube, write_db_cube
from .detection import to_db
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


def _union_grid(inputs: list[Path]) -> tuple[rasterio.crs.CRS, Affine, tuple[int, int], int]:
    """CRS, transform, (height, width) and item size of the union of the Subsets, which share one lattice."""
    left = bottom = float("inf")
    right = top = float("-inf")
    for path in inputs:
        with rasterio.open(path) as src:
            b = src.bounds
            left, bottom, right, top = min(left, b.left), min(bottom, b.bottom), max(right, b.right), max(top, b.top)
            crs, res, itemsize = src.crs, src.res, np.dtype(src.dtypes[0]).itemsize
    transform = Affine(res[0], 0, left, 0, -res[1], top)
    return crs, transform, (round((top - bottom) / res[1]), round((right - left) / res[0])), itemsize


# Converting to dB holds, per pixel and date and variable, the power, the dB and their working copies: 8 B, twice
# the stack's own. Chunks are planned for it once, so one chunk side serves both.
BYTES_PER_DATE_VAR = 8


def _window_params(config: EventConfig, paths: EventPaths, inputs: list[Path]) -> dict:
    """``chunk_side`` and ``window`` for stack_tiles; the window is also saved next to the Cube."""
    crs, transform, shape, _ = _union_grid(inputs)
    dates = len({p.parent.parent.name for p in inputs})
    side = chunks.plan(config.processing.memory_budget_mb, BYTES_PER_DATE_VAR * len(config.series.assets) * dates, *shape).side
    # the margin covers the halo of the speckle filters (Lee then Quegan), so no filtered pixel of the polygon reads a skipped chunk
    settings = config.despeckle
    margin = max(1, settings.lee_window // 2 + settings.quegan_window // 2)
    window = aoi.chunk_window(config.aoi_polygon, crs, transform, shape, side, margin)
    paths.interim.mkdir(parents=True, exist_ok=True)
    paths.window.unlink(missing_ok=True)
    np.savez(paths.window, window=window, side=side)
    print(f"window: {window.sum()}/{window.size} chunks of {side} px touch the polygon ({window.mean():.1%})")
    return {"chunk_side": side, "window": window}


def _write_cube(built: Path, dst: Path, window_params: dict, budget: float) -> tuple[int, int]:
    """Write the stacked linear power *built* to *dst* as int16 dB; returns (chunks stored, chunks in the grid)."""
    if window_params:
        side, window = window_params["chunk_side"], window_params["window"]
        with xr.open_dataset(built) as meta:
            dates, height, width = meta.sizes["time"], meta.sizes["y"], meta.sizes["x"]
            variables = len(meta.data_vars)
        workers = workers_for(budget - chunks.reserved_mb(budget, height, width), BYTES_PER_DATE_VAR * variables * dates, side)
        ds = xr.open_dataset(built, chunks={"time": -1, "y": side, "x": side})
    else:
        window = None
        ds, plan = chunks.open_cube(built, budget, bytes_per_date=BYTES_PER_DATE_VAR * 2)
        workers = plan.workers
    with ds:
        db = xr.Dataset({n: v.copy(data=v.data.map_blocks(to_db, dtype="float32")) for n, v in ds.data_vars.items()}, attrs=ds.attrs)
        stored = write_db_cube(db, dst, window, workers)
        return stored, stored if window is None else window.size


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
    window_params = {}
    if config.aoi_polygon is not None:
        # Only the chunks that touch the polygon are computed and written; the others read back as nodata.
        window_params = _window_params(config, paths, inputs)
    ctx = (
        PipelineComposition(workspace=work)
        .add_step(
            "stack_tiles", MergeBatch(), group=config.event, union=True, time_bin="day",
            memory_budget_mb=config.processing.memory_budget_mb, compress=True, **window_params,
        )
        .run(inputs, save_intermediate=True)
    )
    budget = config.processing.memory_budget_mb
    stored, total = _write_cube(ctx.inputs[0], paths.cube, window_params, budget)
    shutil.rmtree(work, ignore_errors=True)  # the float32 stack
    print(f"cube: {stored}/{total} chunks stored, {paths.cube.stat().st_size / 2**30:.2f} GB")

    ds, plan = chunks.open_cube(paths.cube, budget, bytes_per_date=10)
    with ds, chunks.scheduler(plan.workers, budget):
        acquisitions = len({p.parent.parent.name for p in inputs})
        check_cube(ds, nodata=None, expected=acquisitions)
        print(f"cube: {paths.cube} ({ds.sizes['time']} Acquisitions, variables {list(ds.data_vars)})")
