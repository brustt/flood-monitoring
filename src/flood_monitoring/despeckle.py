"""Reduce speckle in the Cube (Lee, then Quegan) to give the Filtered Cube."""

from __future__ import annotations

import numpy as np
import xarray as xr
from eo_pipe.io.speckle import filter_lazily, lee_block, quegan_block

from . import aoi, chunks
from .config import Despeckle, EventConfig
from .cube import check_cube, db_to_power, georeferenced, write_db_cube
from .detection import to_db
from .paths import EventPaths

# Bytes per pixel and date of a chunk of both variables: the float64 working copies of the filter kernels (32 B each).
BYTES_PER_DATE = 64


def filtered(cube: xr.Dataset, settings: Despeckle) -> xr.Dataset:
    """Lazy dB Filtered Cube of a dB *cube*: each variable goes to linear power per chunk, through Lee then Quegan.

    The filters run on linear power (ADR-0004) in float32; the chunks are read with a halo, so the result does
    not depend on the chunking. Nodata (NaN) is left out of every local statistic and stays nodata.
    """
    if cube.sizes["time"] < 2:
        raise ValueError(f"the Quegan filter needs at least 2 Acquisitions, the Cube has {cube.sizes['time']}")
    out = {}
    for name, var in cube.data_vars.items():
        power = var.data.map_blocks(db_to_power, dtype="float32")
        lee = filter_lazily(
            power, lee_block, settings.lee_window // 2, window=settings.lee_window, enl=settings.enl, nodata=None
        )
        quegan = filter_lazily(lee, quegan_block, settings.quegan_window // 2, window=settings.quegan_window, nodata=None)
        out[name] = var.copy(data=quegan.map_blocks(to_db, dtype="float32"))
    return xr.Dataset(out, coords=cube.coords, attrs=cube.attrs)


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.filtered_cube.exists() and not force:
        print(f"skip: {paths.filtered_cube} exists")
        return
    if not paths.cube.exists():
        raise FileNotFoundError(f"{paths.cube} is missing; run the stack stage first")

    budget = config.processing.memory_budget_mb
    cube, plan = chunks.open_cube(paths.cube, budget, bytes_per_date=BYTES_PER_DATE)
    with cube, chunks.scheduler(plan.workers, budget):
        window = None
        if config.aoi_polygon is not None:
            # Only the chunks that touch the polygon are filtered and stored. The Cube holds a wider window, so
            # their halo is there.
            vv = georeferenced(cube, "VV")
            window = aoi.chunk_window(
                config.aoi_polygon, vv.rio.crs, aoi.written_transform(vv), vv.shape[-2:], plan.side
            )
        paths.window.unlink(missing_ok=True)
        stored = write_db_cube(filtered(cube, config.despeckle), paths.filtered_cube, window, plan.workers)
        if window is not None:
            np.savez(paths.window, window=window, side=plan.side)
        grid = -(-cube.sizes["y"] // plan.side) * -(-cube.sizes["x"] // plan.side)
        print(f"filtered cube: {stored}/{grid} chunks stored, {paths.filtered_cube.stat().st_size / 2**30:.2f} GB")
        expected = cube.sizes["time"]

    filtered_cube, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=10)
    with filtered_cube, chunks.scheduler(plan.workers, budget):
        check_cube(filtered_cube, nodata=None, expected=expected)
        print(f"filtered cube: {paths.filtered_cube} ({filtered_cube.sizes['time']} Acquisitions)")
