"""The Cube and Filtered Cube files: int16 dB x 100 storage, windowed writing, and checks independent of how they were built.

Both are stored as int16 hundredths of a dB (``scale_factor`` 0.01, nodata -32768, zlib) and read back by xarray as
float32 dB with NaN for nodata. Only the speckle filters need linear power: they convert per chunk, in memory.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import dask
import netCDF4
import numpy as np
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from eo_pipe.io.output_types import atomic_path
from xarray.backends.netCDF4_ import NETCDF4_PYTHON_LOCK

DB_SCALE = 0.01  # dB per stored unit
DB_FILL = -32768  # stored nodata


def encode_db(db: np.ndarray) -> np.ndarray:
    """int16 hundredths of a dB of a float32 dB array; NaN becomes :data:`DB_FILL`, which no value can take."""
    info = np.iinfo("int16")
    scaled = np.clip(np.round(np.asarray(db, dtype="float64") / DB_SCALE), info.min + 1, info.max)
    return np.where(np.isnan(db), DB_FILL, scaled).astype("int16")


def db_to_power(db: np.ndarray) -> np.ndarray:
    """Linear power of a dB array, float32; NaN stays NaN."""
    return np.power(np.float32(10), np.asarray(db, dtype="float32") / np.float32(10))


def write_db_cube(ds: xr.Dataset, path: Path, window: np.ndarray | None, workers: int) -> int:
    """Write the dask-backed dB float32 *ds* (time, y, x) to *path* as int16, and return the number of chunks stored.

    The file is chunked like *ds*: its full time axis per spatial chunk. Only the chunks set in *window*, a boolean
    grid over the spatial chunks, are computed and stored; the others take no space and read back as nodata. ``None``
    stores every chunk. *workers* chunks are computed at a time. The file appears at *path* only once complete.
    """
    names = list(ds.data_vars)
    rows, cols = ds[names[0]].chunks[-2:]
    grid = np.ones((len(rows), len(cols)), dtype=bool) if window is None else np.asarray(window, dtype=bool)
    if grid.shape != (len(rows), len(cols)):
        raise ValueError(f"window shape {grid.shape} does not match the chunk grid {(len(rows), len(cols))}")
    out = ds.copy()
    out.attrs.pop("nodata", None)
    for name in names:
        out[name].attrs = {"units": "dB"}
        out[name].encoding = {
            "dtype": "int16", "scale_factor": np.float32(DB_SCALE), "_FillValue": np.int16(DB_FILL),
            "zlib": True, "complevel": 1, "shuffle": True,
            "chunksizes": tuple(c[0] for c in out[name].chunks),
        }
    r_off, c_off = (np.concatenate([[0], np.cumsum(c)]) for c in (rows, cols))
    # xarray's own lock around the netCDF4/HDF5 libraries, which are not thread-safe: the chunks of *ds* may be
    # read from a netCDF file by other threads while this one writes.
    lock = NETCDF4_PYTHON_LOCK
    with atomic_path(path) as tmp:
        out.to_netcdf(tmp, engine="netcdf4", compute=False)  # variables created, no chunk written
        with netCDF4.Dataset(tmp, "r+") as nc:
            for name in names:
                nc[name].set_auto_maskandscale(False)  # we write the packed int16 ourselves

            def write(cell: tuple[int, int]) -> None:
                r, c = cell
                sl = (slice(None), slice(r_off[r], r_off[r + 1]), slice(c_off[c], c_off[c + 1]))
                blocks = dask.compute(*[ds[n].data[sl] for n in names], scheduler="synchronous")
                with lock:
                    for name, block in zip(names, blocks):
                        nc[name][sl] = encode_db(block)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(write, [(int(r), int(c)) for r, c in np.argwhere(grid)]))
    return int(grid.sum())


def check_cube(ds: xr.Dataset, nodata: float | None, expected: int | None = None) -> None:
    """Raise if the time axis is not one step per Acquisition, in order, all with data.

    *expected* is the number of Acquisitions; every variable of every step must hold at
    least one pixel that is neither *nodata* nor NaN. On a chunked dataset, every variable
    is reduced in one pass.
    """
    times = ds["time"].values
    if expected is not None and len(times) != expected:
        raise ValueError(f"cube has {len(times)} time steps, expected {expected}")
    if np.any(np.diff(times) <= np.timedelta64(0, "ns")):
        raise ValueError("cube time axis is not in increasing order")
    reductions = []
    for var in ds.data_vars.values():
        valid = ~np.isnan(var)
        if nodata is not None and not np.isnan(nodata):
            valid &= var != nodata
        reductions.append(valid.any(dim=[d for d in var.dims if d != "time"]))
    for name, has_data in zip(ds.data_vars, dask.compute(*reductions)):
        for i in np.flatnonzero(~np.asarray(has_data)):
            when = np.datetime_as_string(times[i], unit="s")
            raise ValueError(f"cube step {when}: variable {name} has no valid pixel")


def georeferenced(ds: xr.Dataset, name: str) -> xr.DataArray:
    """Variable *name* with its CRS written from the spatial_ref coordinate (eo-pipe keeps the CRS there)."""
    return ds[name].rio.write_crs(ds["spatial_ref"].attrs["spatial_ref"])
