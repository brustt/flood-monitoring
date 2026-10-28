"""Merge the per-Series results into the global Layers on the Event grid (the I/O around :mod:`merge`)."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import dask
import dask.array
import numpy as np
import rasterio
import rasterio.warp
import rioxarray
import xarray as xr
from pyproj import Transformer
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform

from . import aoi, chunks, raster
from .composite import NODATA
from .config import EventConfig
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

    def template(self, side: int) -> xr.DataArray:
        """Georeferenced, lazy (y, x) array on this grid, chunked by *side*; its transform is exactly the grid's."""
        xs = self.transform.c + (np.arange(self.width) + 0.5) * self.transform.a
        ys = self.transform.f + (np.arange(self.height) + 0.5) * self.transform.e
        zeros = dask.array.zeros(self.shape, dtype="uint8", chunks=side)
        da = xr.DataArray(zeros, dims=("y", "x"), coords={"y": ys, "x": xs})
        return da.rio.write_crs(self.crs).rio.write_transform(self.transform)


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


def warp(path: Path, grid: Grid, resampling: Resampling, out: Path, budget_mb: float, nodata: float | None = None) -> None:
    """Every band of the raster at *path* warped onto *grid* by GDAL, file to file *out*.

    Uncovered pixels are *nodata* (or 0). GDAL's working buffer is bounded by the budget's GDAL cache share.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path) as src, rasterio.open(
        out, "w", driver="GTiff", width=grid.width, height=grid.height, count=src.count, dtype=src.dtypes[0],
        crs=grid.crs, transform=grid.transform, nodata=nodata, tiled=True,
        blockxsize=chunks.TILE, blockysize=chunks.TILE,
    ) as dst:
        rasterio.warp.reproject(
            rasterio.band(src, src.indexes),
            rasterio.band(dst, dst.indexes),
            src_nodata=nodata,
            dst_nodata=nodata,
            resampling=resampling,
            warp_mem_limit=chunks.gdal_cache_mb(budget_mb),
        )


def open_on_grid(path: Path, grid: Grid, side: int, mask: bool) -> xr.DataArray:
    """A raster warped onto *grid*, lazily: boolean (y, x) if *mask*, else a (band, y, x) float32 Composite.

    Call ``.close()`` on it once computed.
    """
    opened = rioxarray.open_rasterio(path, chunks={"band": -1, "y": side, "x": side}, mask_and_scale=False)
    if opened.shape[-2:] != grid.shape:
        opened.close()
        raise ValueError(f"{path}: shape {opened.shape[-2:]} differs from the Event grid's {grid.shape}")
    template = grid.template(side)
    da = opened.assign_coords(y=template["y"].values, x=template["x"].values)
    da = da.squeeze("band", drop=True).astype(bool) if mask else da.astype("float32")
    da = da.rio.write_crs(grid.crs).rio.write_transform(grid.transform)
    da.set_close(opened.close)  # derived arrays lose it; left open, the file is closed after GDAL at exit
    return da


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

    layers = [paths.orbit_map, paths.flood_extent, paths.permanent_water, paths.before_composite, paths.after_composite]
    raster.clear(layers, [paths.merge_work, paths.sieve_work])  # the skip sentinels first, then leftovers

    polygon = config.aoi_polygon or box(*config.area_of_interest)
    crs = config.event_grid.crs
    if crs is None:
        with rasterio.open(series[0].flood_extent) as src:
            crs = src.crs
    grid = event_grid(polygon, crs, config.event_grid.pixel_size)
    budget = config.processing.memory_budget_mb
    plan = chunks.plan(budget, 26 * len(series) + 30, grid.height, grid.width)

    rules = config.detection
    with chunks.scheduler(plan.workers, budget):
        warped = []
        for orbit, p in zip(orbits, series):
            work = paths.merge_work / f"orbit-{orbit}"
            files = {}
            for name, src, resampling, nodata in (
                ("flood-extent", p.flood_extent, Resampling.nearest, None),
                ("permanent-water", p.permanent_water, Resampling.nearest, None),
                ("before-composite", p.before_composite, Resampling.bilinear, NODATA),
                ("after-composite", p.after_composite, Resampling.bilinear, NODATA),
            ):
                files[name] = work / f"{name}.tif"
                warp(src, grid, resampling, files[name], budget, nodata)
            warped.append(files)

        opened = {
            name: [open_on_grid(f[name], grid, plan.side, mask=not name.endswith("composite"))
                   for f in warped]
            for name in ("flood-extent", "permanent-water", "before-composite", "after-composite")
        }
        try:
            extents, permanents = opened["flood-extent"], opened["permanent-water"]
            # A Composite pixel missing in any band is nodata in all.
            befores, afters = (
                [c.where(valid_composite(c), NODATA) for c in opened[name]]
                for name in ("before-composite", "after-composite")
            )
            # never name this local aoi: it would shadow the module for the whole function
            inside = aoi.mask_like(polygon, grid.template(plan.side))

            footprints = [valid_composite(b) & valid_composite(a) for b, a in zip(befores, afters)]
            counts, total = dask.compute([(f & inside).sum() for f in footprints], inside.sum())
            shares = coverage([int(c) for c in counts], int(total))
            order = rank_series(shares)
            main = order[0]
            print(f"Main Series: relative orbit {orbits[main]} ({shares[main]:.1%} of the Area of Interest)")

            sieved = raster.sieve(
                [merge_flood_extent(extents, inside)], grid.crs, paths.sieve_work, rules.sieve_size,
                rules.sieve_connectedness, transform=grid.transform,
            )[0]
            sieved_extent = raster.open_mask(sieved, inside, plan.side)
            opened["sieved"] = [sieved_extent]
            extent = sieved_extent & inside
            permanent = merge_flood_extent(permanents, inside)
            before, orbit_map = fill_composites(befores, order, inside)
            after, same_map = fill_composites(afters, order, inside)

            def nodata_outside(mask: xr.DataArray) -> xr.DataArray:
                return xr.where(inside, mask.astype("uint8"), np.uint8(FLOOD_EXTENT_NODATA))

            def layer(data: xr.DataArray, path: Path, dtype: str, nodata: float) -> raster.Raster:
                return raster.Raster(data, path, dtype, grid.crs, nodata, transform=grid.transform)

            # One pass per Layer: in a single pass, dask keeps the intermediates the Layers share (masks of
            # the priority fill, the opened Series rasters) for the whole grid. The warped inputs are local files.
            for data, path, dtype, nodata in (
                (nodata_outside(extent), paths.flood_extent, "uint8", FLOOD_EXTENT_NODATA),
                (nodata_outside(permanent), paths.permanent_water, "uint8", FLOOD_EXTENT_NODATA),
                (before, paths.before_composite, "float32", NODATA),
                (after, paths.after_composite, "float32", NODATA),
                (orbit_map, paths.orbit_map, "uint8", ORBIT_MAP_NODATA),
            ):
                raster.write_partial([layer(data, path, dtype, nodata)])
            seam_differs, n_permanent, n_extent = dask.compute(
                (orbit_map != same_map).any(),  # priority is by footprint, so the seam must match
                permanent.sum(),
                extent.sum(),
            )
        finally:
            for arrays in opened.values():
                for da in arrays:
                    da.close()
    if bool(seam_differs):
        raster.clear(layers)
        raise RuntimeError("Before and After Composites would have different seams")

    shutil.rmtree(paths.merge_work)
    shutil.rmtree(paths.sieve_work)
    raster.commit([paths.permanent_water, paths.before_composite, paths.after_composite])
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
        "permanent_water_km2": round(int(n_permanent) * grid.pixel_km2, 3),
        "flood_extent_km2": round(int(n_extent) * grid.pixel_km2, 3),
    }
    raster.write_json(paths.summary, summary)
    raster.commit([paths.orbit_map, paths.flood_extent])  # Flood Extent last: with the Orbit Map, the skip sentinel
    print(f"Flood Extent {summary['flood_extent_km2']} km2 over {len(orbits)} Series")
