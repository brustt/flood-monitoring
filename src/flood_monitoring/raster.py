"""Raster files of the stages: written as ``.partial`` and renamed on success, sieved file to file, opened lazily."""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dask
import dask.array
import numpy as np
import rioxarray
import xarray as xr
from eo_pipe.steps.raster.sieve import SieveStep
from rasterio.transform import Affine

from .chunks import TILE

ZLEVEL = 6  # zlib default; 9 gains a few percent for several times the write time


@dataclass(frozen=True)
class Raster:
    """One GeoTIFF to write: *data* (y, x) or (band, y, x), cast to *dtype*."""

    data: xr.DataArray
    path: Path  # final path
    dtype: str
    crs: Any
    nodata: float | None = None
    long_name: str | None = None
    transform: Affine | None = None  # exact transform to write; None derives it from the coords
    scale: float | None = None  # GDAL scale tag: stored value times *scale* is the physical value


def partial(path: Path) -> Path:
    """Where *path* is written before :func:`commit` renames it."""
    return path.with_name(path.name + ".partial")


def predictor(dtype: str) -> int:
    """DEFLATE predictor: horizontal differencing (2) for integers, floating point (3) for floats."""
    return 3 if np.issubdtype(np.dtype(dtype), np.floating) else 2


def _prepared(r: Raster) -> xr.DataArray:
    da = r.data if isinstance(r.data.data, dask.array.Array) else r.data.chunk()
    da = da.astype(r.dtype)
    # Attributes inherited from the Cube (e.g. its "nodata") must never reach a GeoTIFF.
    da.attrs = {}
    da.encoding = {}
    da = da.rio.write_crs(r.crs)
    if r.nodata is not None:
        da = da.rio.write_nodata(r.nodata)
    if r.scale is not None:
        da.attrs["scale_factor"], da.attrs["add_offset"] = r.scale, 0.0
    if r.long_name is not None:
        da.attrs["long_name"] = r.long_name
    if r.transform is not None:
        # without x/y coords rioxarray writes this transform as is instead of deriving one from them
        da = da.drop_vars(["x", "y"], errors="ignore").rio.write_transform(r.transform)
    return da


def write_partial(rasters: list[Raster], *extra) -> tuple:
    """Write every Raster to its partial path and compute *extra* in the same dask pass; returns *extra* computed."""
    writes = []
    for r in rasters:
        target = partial(r.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        writes.append(
            _prepared(r).rio.to_raster(
                target, driver="GTiff", dtype=r.dtype, tiled=True, blockxsize=TILE, blockysize=TILE,
                compress="deflate", zlevel=ZLEVEL, predictor=predictor(r.dtype), bigtiff="if_safer",
                lock=threading.Lock(), compute=False, recalc_transform=r.transform is None,
            )
        )
    return dask.compute(*writes, *extra)[len(writes) :]


def commit(paths: list[Path]) -> None:
    """Rename each partial file to its final path, in order."""
    for p in paths:
        os.replace(partial(p), p)


def clear(paths: list[Path], folders: list[Path] = ()) -> None:
    """Delete each path and its partial file, and each folder; missing ones are ignored."""
    for p in paths:
        p.unlink(missing_ok=True)
        partial(p).unlink(missing_ok=True)
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)


def write_json(path: Path, obj: dict) -> None:
    """*obj* as indented JSON, written atomically."""
    partial(path).write_text(json.dumps(obj, indent=2) + "\n")
    os.replace(partial(path), path)


def sieve(
    masks: list[xr.DataArray], crs: Any, work: Path, size: int, connectedness: int, transform: Affine | None = None
) -> list[Path]:
    """eo-pipe's (GDAL) sieve of each boolean (y, x) mask, judged on the whole mask; returns the sieved files.

    The masks go through uint8 GeoTIFFs in *work*, which is emptied first; the raw files are removed
    once sieved. The sieved files are left in ``work/sieved`` for the caller.
    """
    shutil.rmtree(work, ignore_errors=True)
    raw = [work / "raw" / f"{i}.tif" for i in range(len(masks))]
    write_partial([Raster(m, p, "uint8", crs, transform=transform) for m, p in zip(masks, raw)])
    commit(raw)
    (work / "sieved").mkdir()
    result = SieveStep().execute(raw, work / "sieved", threshold=size, connectedness=connectedness)
    shutil.rmtree(work / "raw")
    return [Path(o.path) for o in result.outputs]


def open_mask(path: Path, like: xr.DataArray, side: int) -> xr.DataArray:
    """Band 1 of the mask at *path* as a lazy boolean (y, x) array on the coords and CRS of *like*.

    Call ``.close()`` on it once computed.
    """
    opened = rioxarray.open_rasterio(path, chunks={"band": 1, "y": side, "x": side})
    da = opened.squeeze("band", drop=True)
    if da.shape != like.shape[-2:]:
        opened.close()
        raise ValueError(f"{path}: shape {da.shape} differs from the grid's {like.shape[-2:]}")
    da = da.astype(bool).assign_coords(y=like["y"].values, x=like["x"].values).rio.write_crs(like.rio.crs)
    da.set_close(opened.close)  # derived arrays lose it; left open, the file is closed after GDAL at exit
    return da


def read_mask(path: Path) -> np.ndarray:
    """Boolean (y, x) array of band 1 (tests and small rasters only)."""
    with rioxarray.open_rasterio(path) as da:
        return da.values[0].astype(bool)
