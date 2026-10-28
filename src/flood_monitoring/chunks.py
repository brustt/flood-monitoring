"""Chunk planning from the memory budget, chunked opening of a Cube, and the dask + GDAL settings of a stage.

Stages call ``chunks.plan`` / ``chunks.open_cube`` / ``chunks.scheduler`` through the module (tests patch ``plan``).
"""

from __future__ import annotations

import math
import warnings
from contextlib import contextmanager
from pathlib import Path

import dask
import rasterio
import xarray as xr
from eo_pipe.io.memory import MIN_CHUNK_SIDE, OVERHEAD, ChunkPlan, plan_chunks

TILE = 256  # GeoTIFF block size of every raster we write
_MB = 2**20


def gdal_cache_mb(budget_mb: float) -> int:
    """Block cache of each of the two GDAL libraries, MB; also the warp memory limit of the merge."""
    return max(32, int(budget_mb // 16))


def reserved_mb(budget_mb: float, height: int, width: int) -> float:
    """Memory outside the chunks: both GDAL caches and the whole-grid Area of Interest mask (1 B/px)."""
    return 2 * gdal_cache_mb(budget_mb) + height * width / _MB


def _budget_needed(minimal: float, height: int, width: int) -> int:
    """Smallest whole budget whose share left after :func:`reserved_mb` holds *minimal* MB."""

    def fits(budget: int) -> bool:
        return budget - reserved_mb(budget, height, width) >= minimal

    budget = math.ceil(minimal + reserved_mb(0, height, width))
    while not fits(budget):  # the caches grow with the budget past 512 MB
        budget = max(budget + 1, math.ceil((minimal + height * width / _MB) * 8 / 7))
    while budget > 1 and fits(budget - 1):
        budget -= 1
    return budget


def _align(side: int, stored: int) -> int:
    """*side* shrunk so that each chunk reads whole stored chunks (or a whole divisor of one)."""
    if side >= stored:
        return side - side % stored
    divisors = [d for d in range(MIN_CHUNK_SIDE, side + 1) if stored % d == 0]
    return divisors[-1] if divisors else side


def plan(budget_mb: float, bytes_per_pixel: float, height: int, width: int, stored: int = TILE) -> ChunkPlan:
    """Chunk side and concurrency of a (height, width) grid under *budget_mb*, net of the GDAL caches and AOI mask."""
    reserved = reserved_mb(budget_mb, height, width)
    left = budget_mb - reserved
    minimal = bytes_per_pixel * OVERHEAD * MIN_CHUNK_SIDE**2 / _MB
    if left < minimal:
        raise ValueError(
            f"memory_budget_mb={budget_mb:g} is too small: {reserved:.0f} MB go to the GDAL caches and the "
            f"Area of Interest mask and one minimal chunk needs {minimal:.3f} MB; set memory_budget_mb to at least "
            f"{_budget_needed(minimal, height, width)}"
        )
    p = plan_chunks(left, bytes_per_pixel, height, width)
    side = _align(p.side, stored)
    print(f"chunks: {side} px, {p.workers} at a time")
    return ChunkPlan(side, p.workers)


def open_cube(
    path: Path, budget_mb: float, bytes_per_date: float, time_chunk: int = -1
) -> tuple[xr.Dataset, ChunkPlan]:
    """The (Filtered) Cube at *path*, opened lazily in spatial chunks planned from *budget_mb*."""
    with xr.open_dataset(path) as meta:
        sizes = dict(meta.sizes)
        stored_chunks = meta["VV"].encoding.get("chunksizes") if "VV" in meta else None
    stored = stored_chunks[-1] if stored_chunks and stored_chunks[-1] == stored_chunks[-2] else TILE
    bytes_per_pixel = bytes_per_date * (sizes["time"] if time_chunk == -1 else time_chunk)
    p = plan(budget_mb, bytes_per_pixel, sizes["y"], sizes["x"], stored)
    with warnings.catch_warnings():
        # xarray warns whenever a chunk splits a stored chunk: a side dividing the stored one, or one date of the
        # time axis (composite). On eo-pipe's uncompressed NetCDF that only means a partial read of a stored chunk.
        warnings.filterwarnings("ignore", message="The specified chunks separate the stored chunks", category=UserWarning)
        ds = xr.open_dataset(path, chunks={"time": time_chunk, "y": p.side, "x": p.side})
    return ds, p


@contextmanager
def scheduler(workers: int, budget_mb: float):
    """Threaded dask with *workers* threads, and the block caches of both GDAL libraries bounded.

    rasterio bundles its own GDAL; osgeo (eo-pipe's sieve) loads the system one.
    """
    from osgeo import gdal

    cache_bytes = gdal_cache_mb(budget_mb) * _MB
    old = gdal.GetCacheMax()
    gdal.SetCacheMax(cache_bytes)
    try:
        with dask.config.set(scheduler="threads", num_workers=workers), rasterio.Env(GDAL_CACHEMAX=cache_bytes):
            yield
    finally:
        gdal.SetCacheMax(old)
