"""Merge the per-Series results into the global Layers on the Event grid (the I/O around :mod:`merge`)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
import xarray as xr
from pyproj import Transformer
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform

from .aoi import aoi_mask
from .composite import NODATA
from .config import EventConfig
from .flood_extent import sieve_masks
from .merge import ORBIT_MAP_NODATA, coverage, fill_composites, merge_flood_extent, rank_series, valid_composite
from .paths import EventPaths

FLOOD_EXTENT_NODATA = 255  # as declared by the export stage


@dataclass(frozen=True)
class Grid:
    crs: rasterio.crs.CRS
    transform: Affine
    height: int
    width: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def pixel_km2(self) -> float:
        return abs(self.transform.a * self.transform.e) / 1e6

    def template(self) -> xr.DataArray:
        """Georeferenced (y, x) array on this grid, for the sieve."""
        xs = self.transform.c + (np.arange(self.width) + 0.5) * self.transform.a
        ys = self.transform.f + (np.arange(self.height) + 0.5) * self.transform.e
        da = xr.DataArray(np.zeros(self.shape, "uint8"), dims=("y", "x"), coords={"y": ys, "x": xs})
        return da.rio.write_crs(self.crs)


def event_grid(polygon, crs, pixel_size: float) -> Grid:
    """Grid covering *polygon* (EPSG:4326) in *crs*, snapped to multiples of *pixel_size*."""
    crs = rasterio.crs.CRS.from_user_input(crs)
    if crs.is_geographic:
        raise ValueError(f"the Event grid needs a projected CRS (metres), got {crs}")
    project = Transformer.from_crs(4326, crs, always_xy=True).transform
    west, south, east, north = shapely_transform(project, polygon).bounds
    west, north = math.floor(west / pixel_size) * pixel_size, math.ceil(north / pixel_size) * pixel_size
    width, height = math.ceil((east - west) / pixel_size), math.ceil((north - south) / pixel_size)
    return Grid(crs, from_origin(west, north, pixel_size, pixel_size), height, width)


def reproject(path: Path, grid: Grid, resampling: Resampling, nodata: float | None = None) -> np.ndarray:
    """All bands of the raster at *path* on *grid*, as a (bands, y, x) array filled with *nodata* (or 0)."""
    with rasterio.open(path) as src:
        data = src.read()
        out = np.full((src.count, *grid.shape), 0 if nodata is None else nodata, dtype=data.dtype)
        rasterio.warp.reproject(
            data,
            out,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            resampling=resampling,
            src_nodata=nodata,
            dst_nodata=nodata,
        )
    return out


def write_raster(path: Path, data: np.ndarray, grid: Grid, nodata: float) -> None:
    bands = data if data.ndim == 3 else data[None]
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=grid.width, height=grid.height, count=len(bands),
        dtype=bands.dtype, crs=grid.crs, transform=grid.transform, nodata=nodata,
    ) as dst:
        dst.write(bands)


def load_composite(path: Path, grid: Grid) -> np.ndarray:
    """A Composite on the Event grid; a pixel missing in any band is nodata in all."""
    comp = reproject(path, grid, Resampling.bilinear, NODATA).astype("float32")
    comp[:, ~valid_composite(comp)] = NODATA
    return comp


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    orbits = config.series.relative_orbits
    if len(orbits) == 1:
        print("skip: a single Series already gives the global Layers")
        return
    if paths.orbit_map.exists() and paths.flood_extent.exists() and not force:
        print(f"skip: {paths.orbit_map} exists")
        return

    series = [paths.series_paths(o) for o in orbits]
    summaries = []
    for orbit, p in zip(orbits, series):
        for needed in (p.summary, p.flood_extent, p.permanent_water, p.before_composite, p.after_composite):
            if not needed.exists():
                raise FileNotFoundError(f"{needed} is missing; run the stages of relative orbit {orbit} first")
        summaries.append(json.loads(p.summary.read_text()))
        if "peak" not in summaries[-1]:
            raise FileNotFoundError(f"{p.summary} has no Peak; run the flood-extent stage first")

    polygon = config.aoi_polygon or box(*config.area_of_interest)
    crs = config.event_grid.crs
    if crs is None:
        with rasterio.open(series[0].flood_extent) as src:
            crs = src.crs
    grid = event_grid(polygon, crs, config.event_grid.pixel_size)
    aoi = aoi_mask(polygon, grid.crs, grid.transform, grid.shape)

    nearest = Resampling.nearest
    extents = [reproject(p.flood_extent, grid, nearest)[0].astype(bool) for p in series]
    permanents = [reproject(p.permanent_water, grid, nearest)[0].astype(bool) for p in series]
    befores = [load_composite(p.before_composite, grid) for p in series]
    afters = [load_composite(p.after_composite, grid) for p in series]

    footprints = [valid_composite(b) & valid_composite(a) for b, a in zip(befores, afters)]
    order = rank_series(footprints, aoi)
    shares = coverage(footprints, aoi)
    main = order[0]
    print(f"Main Series: relative orbit {orbits[main]} ({shares[main]:.1%} of the Area of Interest)")

    rules = config.detection
    extent = merge_flood_extent(extents, aoi)
    extent = sieve_masks(extent[None], grid.template(), paths.sieve_work, rules.sieve_size, rules.sieve_connectedness)[0]
    extent &= aoi
    permanent = np.logical_or.reduce(permanents) & aoi
    before, orbit_map = fill_composites(befores, order, aoi)
    after, same_map = fill_composites(afters, order, aoi)
    if not np.array_equal(orbit_map, same_map):  # priority is by footprint, so the seam must match
        raise RuntimeError("Before and After Composites would have different seams")

    write_raster(paths.flood_extent, np.where(aoi, extent, FLOOD_EXTENT_NODATA).astype("uint8"), grid, FLOOD_EXTENT_NODATA)
    write_raster(paths.permanent_water, np.where(aoi, permanent, FLOOD_EXTENT_NODATA).astype("uint8"), grid, FLOOD_EXTENT_NODATA)
    write_raster(paths.before_composite, before, grid, NODATA)
    write_raster(paths.after_composite, after, grid, NODATA)
    write_raster(paths.orbit_map, orbit_map, grid, ORBIT_MAP_NODATA)
    paths.aoi_polygon.write_text(
        json.dumps({"type": "Feature", "properties": {}, "geometry": mapping(polygon)}) + "\n"
    )

    main_summary = summaries[main]
    summary = {
        "event": config.event,
        "main_series": orbits[main],
        "event_start": main_summary["event_start"],
        "before": main_summary["before"],
        "peak": main_summary["peak"],
        "series": [
            {
                "relative_orbit": orbits[i],
                "rank": rank,
                "coverage_share": round(shares[i], 4),
                "event_start": summaries[i]["event_start"],
                "before": summaries[i]["before"],
                "peak": summaries[i]["peak"],
                "flooded_area_km2": summaries[i]["flooded_area_km2"],
                "flood_extent_km2": summaries[i]["flood_extent_km2"],
            }
            for rank, i in enumerate(order, start=1)
        ],
        "permanent_water_km2": round(int(permanent.sum()) * grid.pixel_km2, 3),
        "flood_extent_km2": round(int(extent.sum()) * grid.pixel_km2, 3),
    }
    paths.summary.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Flood Extent {summary['flood_extent_km2']} km2 over {len(orbits)} Series")
