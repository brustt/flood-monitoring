"""Chunk planning from the memory budget, chunked opening of a Cube, and the dask + GDAL settings of a stage.

Stages call ``chunks.plan`` / ``chunks.open_cube`` / ``chunks.scheduler`` through the module (tests patch ``plan``).

The planner is a copy of eo-pipe's (``eo_pipe.io.memory``, ADR-0004): importing eo-pipe loads every step and OTB, and
the load stage must run without it (#43). ``tests/test_chunks.py`` checks the copy against eo-pipe when installed.
"""

from __future__ import annotations

import math
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import dask
import rasterio
import xarray as xr
from dask.system import CPU_COUNT

TILE = 256  # GeoTIFF block size of every raster we write
_MB = 2**20
MIN_CHUNK_SIDE = 16  # smallest square chunk the planner produces
OVERHEAD = 3.0  # multiple of a chunk's payload held at once (input window + output block + write copy)
_PREFERRED_SIDE = 128  # prefer at least this chunk side over running more chunks at once


@dataclass(frozen=True)
class ChunkPlan:
    """Square spatial chunk of ``side`` pixels, ``workers`` of them processed at once."""

    side: int
    workers: int


def _min_budget_error(budget: float, needed_bytes: float) -> ValueError:
    return ValueError(
        f"memory_budget_mb={budget:g} is too small for even one minimal chunk "
        f"({MIN_CHUNK_SIDE} pixel rows or a {MIN_CHUNK_SIDE}x{MIN_CHUNK_SIDE} chunk); set memory_budget_mb to at least "
        f"{needed_bytes / _MB:.3f}."
    )


def plan_chunks(budget: float, bytes_per_pixel: float, height: int, width: int) -> ChunkPlan:
    """Largest square chunk (and concurrency) whose estimated peak fits *budget* MB.

    *bytes_per_pixel* is the payload of one pixel across everything a chunk holds (time axis x variables x
    itemsize). Concurrency is reduced before the chunk is shrunk below a sensible size.
    """
    per_pixel = bytes_per_pixel * OVERHEAD
    minimal = per_pixel * MIN_CHUNK_SIDE**2
    if budget * _MB < minimal:
        raise _min_budget_error(budget, minimal)
    cap = max(height, width, 1)
    side, workers = MIN_CHUNK_SIDE, 1
    for w in range(max(1, CPU_COUNT), 0, -1):
        side = int(math.sqrt(budget * _MB / (w * per_pixel)))
        workers = w
        if side >= min(_PREFERRED_SIDE, cap):
            break
    side = min(side, cap)
    if side > MIN_CHUNK_SIDE:
        side -= side % MIN_CHUNK_SIDE
    return ChunkPlan(side=max(side, 1), workers=workers)


def workers_for(budget: float, bytes_per_pixel: float, side: int) -> int:
    """How many ``side`` x ``side`` chunks fit *budget* MB at once (at least 1)."""
    one = bytes_per_pixel * OVERHEAD * side * side
    if budget * _MB < one:
        raise _min_budget_error(budget, one)
    return max(1, min(CPU_COUNT, int(budget * _MB / one)))


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

    rasterio bundles its own GDAL; osgeo (eo-pipe's sieve) loads the system one, absent from the light install
    (#43), where only rasterio's is bounded.
    """
    try:
        from osgeo import gdal
    except ImportError:
        gdal = None

    cache_bytes = gdal_cache_mb(budget_mb) * _MB
    if gdal is not None:
        old = gdal.GetCacheMax()
        gdal.SetCacheMax(cache_bytes)
    try:
        with dask.config.set(scheduler="threads", num_workers=workers), rasterio.Env(GDAL_CACHEMAX=cache_bytes):
            yield
    finally:
        if gdal is not None:
            gdal.SetCacheMax(old)
