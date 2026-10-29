"""Deterministic synthetic inputs of the reference outputs, the tests and the memory gate (docs/xarray-dask-rewrite.md).

Every fraction ``f`` of a size ``n`` becomes the pixel index ``int(round(f * n))``; ranges are half-open.
Runs on the code before and after the xarray/dask rewrite (``processing`` is set only if the config has it).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box
from shapely.ops import transform as shapely_transform

from flood_monitoring import config as config_module
from flood_monitoring.composite import SCALE, STORED_NODATA
from flood_monitoring.config import EventConfig, EventGrid, Series, Window
from flood_monitoring.paths import EventPaths

EVENT = "synthetic-2026-01"
QUAD = ((2, 3), (45, 1), (46, 44), (1, 46))  # AOI corners, (col, row) on a 48-px grid; scaled by size / 48
CRS = "EPSG:32630"
ORIGIN = (500000.0, 5000000.0)
PIXEL = 10.0
CUBE_NODATA = -32768.0
WET = 0.005  # linear power, about -23 dB


def _i(fraction: float, n: int) -> int:
    return int(round(fraction * n))


def _to_lonlat(geom):
    return shapely_transform(Transformer.from_crs(CRS, 4326, always_xy=True).transform, geom)


def grid_polygon(size: int, x_shift_m: float = 0.0) -> Polygon:
    """QUAD scaled to *size* (pixel corners) on the EPSG:32630 grid, in EPSG:4326."""
    x0, y0 = ORIGIN[0] + x_shift_m, ORIGIN[1]
    corners = [(x0 + col * size / 48 * PIXEL, y0 - row * size / 48 * PIXEL) for col, row in QUAD]
    return _to_lonlat(Polygon(corners))


def config(
    *,
    dates: list[date],
    budget_mb: float | None = None,
    aoi_polygon=None,
    orbits=(8,),
    event_grid_crs=None,
    size: int = 48,
) -> EventConfig:
    if aoi_polygon is not None:
        bounds = aoi_polygon.bounds
    else:
        bounds = _to_lonlat(box(ORIGIN[0], ORIGIN[1] - size * PIXEL, ORIGIN[0] + size * PIXEL, ORIGIN[1])).bounds
    extra = {}
    if budget_mb is not None and "processing" in EventConfig.__dataclass_fields__:
        extra["processing"] = config_module.Processing(budget_mb)
    return EventConfig(
        project="flood-monitoring",
        event=EVENT,
        area_of_interest=tuple(bounds),
        window=Window(dates[0], dates[-1]),
        series=Series("sentinel-1-rtc", dates[0], dates[-1], "descending", "IW", tuple(orbits), ("vv", "vh")),
        event_grid=EventGrid(crs=event_grid_crs),
        aoi_polygon=aoi_polygon,
        **extra,
    )


def paths(root: Path) -> EventPaths:
    return EventPaths(EVENT, data_root=root, repo_root=root / "repo")


def cube_dates(n: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def write_cube(
    path: Path,
    *,
    size: int,
    dates: int,
    chunk: int,
    flood_from: int,
    flood_fraction: float,
    salt_fraction: float,
    seed: int = 26,
) -> None:
    """A (Filtered) Cube in eo-pipe's format: VV, VH float32 (time, y, x) linear power."""
    rng = np.random.default_rng(seed)
    vv = np.empty((dates, size, size), dtype="float32")
    for t in range(dates):  # same stream as one (dates, size, size) draw, without the float64 copy
        vv[t] = 0.1 * (1 + 0.2 * (rng.random((size, size)) - 0.5))
    vh = vv * np.float32(0.25)
    rows = slice(_i(0.25, size), _i(0.75, size))
    cols = slice(_i(0.25, size), _i(0.25 + flood_fraction, size))
    river = slice(_i(0.85, size), max(_i(0.875, size), _i(0.85, size) + 1))
    salt = rng.random((size, size)) < salt_fraction
    square = slice(_i(0.83, size), _i(0.92, size))
    for arr in (vv, vh):
        arr[flood_from:, rows, cols] = WET
        arr[:, river, :] = WET
        arr[:, salt] = WET
        arr[:, :, _i(0.0625, size)] = np.nan
        arr[:, square, square] = CUBE_NODATA

    times = np.array([np.datetime64(d, "ns") for d in cube_dates(dates)])
    ds = xr.Dataset(
        {"VV": (("time", "y", "x"), vv), "VH": (("time", "y", "x"), vh)},
        coords={
            "time": times,
            "y": ORIGIN[1] - PIXEL * (np.arange(size) + 0.5),
            "x": ORIGIN[0] + PIXEL * (np.arange(size) + 0.5),
        },
    )
    ds = ds.rio.write_crs(CRS).rio.write_coordinate_system()
    side = min(chunk, size)
    for var in ("VV", "VH"):
        ds[var].encoding["_FillValue"] = CUBE_NODATA
        ds[var].encoding.pop("grid_mapping", None)
        ds[var].attrs.pop("grid_mapping", None)
        ds[var].attrs["nodata"] = CUBE_NODATA
        ds[var].encoding["chunksizes"] = (dates, side, side)
    ds.attrs["nodata"] = CUBE_NODATA
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)


def single_series_config(
    root: Path, *, size: int = 48, dates: int = 20, budget_mb: float | None = None
) -> tuple[EventConfig, EventPaths]:
    cfg = config(dates=cube_dates(dates), budget_mb=budget_mb, aoi_polygon=grid_polygon(size), size=size)
    return cfg, paths(root)


def single_series(
    root: Path,
    *,
    size: int = 48,
    dates: int = 20,
    chunk: int = 16,
    flood_from: int = 12,
    flood_fraction: float = 0.5,
    salt_fraction: float = 0.01,
    budget_mb: float | None = None,
) -> tuple[EventConfig, EventPaths]:
    """A Filtered Cube whose Event Start is date index *flood_from*, with its config and paths."""
    p = paths(root)
    write_cube(
        p.filtered_cube, size=size, dates=dates, chunk=chunk, flood_from=flood_from,
        flood_fraction=flood_fraction, salt_fraction=salt_fraction,
    )
    return single_series_config(root, size=size, dates=dates, budget_mb=budget_mb)


def _write(path: Path, data: np.ndarray, transform, nodata) -> None:
    bands = data if data.ndim == 3 else data[None]
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=bands.shape[2], height=bands.shape[1], count=len(bands),
        dtype=bands.dtype, crs=CRS, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(bands)


def write_series(
    paths: EventPaths,
    *,
    size: int,
    x_shift_m: float,
    valid_cols: tuple[float, float],
    flood_box: tuple[float, float, float, float],
    before: tuple[float, float],
    after: tuple[float, float],
    summary: dict,
) -> None:
    """Per-Series processed rasters (Composites, Flood Extent, Permanent Water) and summary of one orbit."""
    transform = from_origin(ORIGIN[0] + x_shift_m, ORIGIN[1], PIXEL, PIXEL)
    valid = np.zeros((size, size), bool)
    valid[:, _i(valid_cols[0], size) : _i(valid_cols[1], size)] = True

    def composite(vv: float, vh: float) -> np.ndarray:
        bands = np.round(np.array([vv, vh, vv - vh]) / SCALE).astype("int16")[:, None, None]
        return np.where(valid, bands, np.int16(STORED_NODATA)).astype("int16")

    _write(paths.before_composite, composite(*before), transform, STORED_NODATA)
    _write(paths.after_composite, composite(*after), transform, STORED_NODATA)
    r0, r1, c0, c1 = (_i(f, size) for f in flood_box)
    flooded = np.zeros((size, size), "uint8")
    flooded[r0:r1, c0:c1] = 1
    _write(paths.flood_extent, flooded, transform, None)
    permanent = np.zeros((size, size), "uint8")
    permanent[_i(0.85, size) : max(_i(0.875, size), _i(0.85, size) + 1)] = 1
    _write(paths.permanent_water, permanent, transform, None)
    paths.summary.write_text(json.dumps(summary, indent=2) + "\n")


def two_series_config(root: Path, *, size: int = 48, budget_mb: float | None = None) -> tuple[EventConfig, EventPaths]:
    cfg = config(
        dates=cube_dates(20), budget_mb=budget_mb, aoi_polygon=grid_polygon(size), orbits=(8, 110),
        event_grid_crs="EPSG:2154", size=size,
    )
    return cfg, paths(root)


def two_series(root: Path, *, size: int = 48, budget_mb: float | None = None) -> tuple[EventConfig, EventPaths]:
    """Two Series (orbits 8 and 110) of processed rasters whose valid footprints overlap on a third of the grid."""
    p = paths(root)
    write_series(
        p.series_paths(8), size=size, x_shift_m=0, valid_cols=(0, 2 / 3), flood_box=(0.2, 0.4, 0.05, 0.25),
        before=(-10, -16), after=(-12, -18),
        summary={"event": EVENT, "event_start": "2026-01-13", "before": "2026-01-12", "peak": "2026-01-15",
                 "flooded_area_km2": {"2026-01-13": 0.1}, "flood_extent_km2": 0.1},
    )
    write_series(
        p.series_paths(110), size=size, x_shift_m=35, valid_cols=(1 / 3, 1), flood_box=(0.2, 0.4, 0.6, 0.8),
        before=(-20, -26), after=(-22, -28),
        summary={"event": EVENT, "event_start": "2026-01-14", "before": "2026-01-13", "peak": "2026-01-16",
                 "flooded_area_km2": {"2026-01-14": 0.1}, "flood_extent_km2": 0.1},
    )
    return two_series_config(root, size=size, budget_mb=budget_mb)


def read_outputs(processed: Path) -> tuple[dict[str, np.ndarray], dict[str, dict]]:
    """Every raster under *processed* (Series inputs in ``orbit-*`` left out), keyed by relative path: arrays and meta."""
    arrays, meta = {}, {}
    for path in sorted(processed.rglob("*.tif")):
        key = path.relative_to(processed).as_posix()
        if key.startswith("orbit-"):
            continue
        with rasterio.open(path) as src:
            arrays[key] = src.read()
            meta[key] = {
                "crs": src.crs.to_wkt(),
                "transform": list(src.transform)[:6],
                "dtype": src.dtypes[0],
                "nodata": src.nodata,
                "count": src.count,
                "tags": src.tags(),
            }
    return arrays, meta
