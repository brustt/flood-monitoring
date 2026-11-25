"""Estimate the disk a Series needs, per relative orbit, from the STAC footprints and the AOI polygon.

Sizes follow what the pipeline stores (measured on charente-2026-02, issue #39):
  raw       Subsets are the slice cut to the AOI bbox (measured: the blocks outside the polygon are not skipped):
            9.4 B per pixel (vv + vh, DEFLATE float32)
  stack     float32 stack of the 256 px chunks that touch the polygon, 8 B per pixel and date (peak only)
  Cube      int16 dB, zlib: 2.4 B per pixel of the 256 px chunks touched, per date (1.93 GB / (22.7 Mpx x 35 dates))
  Filtered  int16 dB, zlib: 2.8 B per polygon pixel and date (1.32 GB / (13.3 Mpx x 35 dates))
The footprints include nodata corners, so the raw figure is slightly high.

Then the COG blocks fetched over the network (issue #42), counted on each item's 512 px block grid (data blocks only:
those touching the item footprint; the nodata blocks are a few kB):
  raw       blocks overlapping the AOI bbox, what the download of the raw Subsets reads
  odc       odc-stac building the Cube straight from the COGs (prototypes/odc_stac_cube.py) at 512 px chunks on the
            grid of the polygon bounds in the orbit's main UTM zone, reading only the chunks of aoi.chunk_window
            (Lee + Quegan margin): unique blocks, and fetches = one per (window chunk, block) pair, since the item grid
            is not aligned with the chunks and each chunk read opens the COG anew (fetches without any cache)
  GB        blocks x 2 assets x BLOCK_B, the mean compressed block measured by experiments/pc_throughput.py on data
            blocks; partly-nodata edge blocks are smaller, so this is slightly high

Usage: uv run python experiments/estimate_disk.py <aoi.gpkg> <start> <end> <orbit> [<orbit> ...]
"""
import math
import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.transform import Affine, from_origin
from shapely.geometry import mapping, shape

from flood_monitoring.aoi import chunk_window

sys.path.insert(0, str(Path(__file__).parent))
from s1_orbits_for_aoi import METRIC_CRS, crs_of, search  # noqa: E402

CELL = 640.0  # m, one 64 px chunk of the Filtered Cube; 256 px chunks = 4 cells
RAW_B_PX, STACK_B_PX_DATE, CUBE_B_PX_DATE, FILTERED_B_PX_DATE = 9.4, 8.0, 2.4, 2.8
PX = 100.0  # m2 per 10 m pixel
BLOCK, RES = 512, 10.0  # COG block side (px) = odc-stac chunk side; pixel size (m)
BLOCK_B = 851e3  # mean compressed bytes per data block, measured by experiments/pc_throughput.py
MARGIN = 7 // 2 + 7 // 2  # lee_window // 2 + quegan_window // 2 of the Event YAML, as in the stack stage


def _pool(mask: np.ndarray, k: int) -> np.ndarray:
    """True for every cell of a k x k group that holds a True cell."""
    h, w = (-(-s // k) * k for s in mask.shape)
    padded = np.zeros((h, w), bool)
    padded[: mask.shape[0], : mask.shape[1]] = mask
    pooled = padded.reshape(h // k, k, w // k, k).any(axis=(1, 3))
    return np.kron(pooled, np.ones((k, k), bool))[: mask.shape[0], : mask.shape[1]]


def cube_grid(polygon, crs: str) -> tuple[Affine, tuple[int, int]]:
    """Grid of odc-stac loading the polygon bounds (EPSG:4326) in *crs* at 10 m, snapped outward to the pixel size."""
    x0, y0, x1, y1 = Transformer.from_crs(4326, crs, always_xy=True).transform_bounds(*polygon.bounds, densify_pts=21)
    x0, y1 = math.floor(x0 / RES) * RES, math.ceil(y1 / RES) * RES
    return Affine(RES, 0, x0, 0, -RES, y1), (math.ceil((y1 - y0) / RES), math.ceil((x1 - x0) / RES))


def _block_ranges(item: dict, crs: str, transform: Affine, side: int) -> tuple[np.ndarray, ...]:
    """Per block of *item*: the [first, last) rows and columns of the *side* px cells of the grid (*crs*, *transform*)
    that its bounds overlap (bounds of the reprojected corners when the CRSs differ)."""
    a, _, x0, _, e, y0 = item["properties"]["proj:transform"][:6]
    h, w = item["properties"]["proj:shape"]
    xs = x0 + np.arange(-(-w // BLOCK) + 1) * BLOCK * a
    ys = y0 + np.arange(-(-h // BLOCK) + 1) * BLOCK * e
    gx, gy = np.meshgrid(np.minimum(xs, x0 + w * a), np.maximum(ys, y0 + h * e))  # last blocks are partial
    if crs != crs_of(item):
        gx, gy = Transformer.from_crs(crs_of(item), crs, always_xy=True).transform(gx, gy)
    col, row = ~transform * (gx, gy)
    corners = lambda v, f: f(f(v[:-1, :-1], v[1:, :-1]), f(v[:-1, 1:], v[1:, 1:]))  # noqa: E731
    r0, r1 = np.floor(corners(row, np.minimum) / side), np.ceil(corners(row, np.maximum) / side)
    c0, c1 = np.floor(corners(col, np.minimum) / side), np.ceil(corners(col, np.maximum) / side)
    return r0, r1, c0, c1


def _data_blocks(item: dict) -> np.ndarray:
    """(rows, cols) mask of the item's blocks that touch its footprint."""
    a, _, x0, _, e, y0 = item["properties"]["proj:transform"][:6]
    h, w = item["properties"]["proj:shape"]
    fp = gpd.GeoSeries([shape(item["geometry"])], crs="EPSG:4326").to_crs(crs_of(item)).iloc[0]
    return features.rasterize([(fp, 1)], (-(-h // BLOCK), -(-w // BLOCK)), all_touched=True,
                              transform=Affine(BLOCK * a, 0, x0, 0, BLOCK * e, y0)).astype(bool)


def window_blocks(item: dict, crs: str, transform: Affine, window: np.ndarray) -> tuple[int, int]:
    """Data blocks of *item* read by the chunks of *window* (grid *crs*, *transform*, *BLOCK* px chunks):
    (unique blocks, fetches = sum over blocks of the window chunks they overlap)."""
    r0, r1, c0, c1 = (v.astype(int) for v in _block_ranges(item, crs, transform, BLOCK))
    rows, cols = window.shape
    r0, r1, c0, c1 = np.clip(r0, 0, rows), np.clip(r1, 0, rows), np.clip(c0, 0, cols), np.clip(c1, 0, cols)
    sat = np.zeros((rows + 1, cols + 1), np.int64)
    sat[1:, 1:] = window.cumsum(0).cumsum(1)
    n = sat[r1, c1] - sat[r0, c1] - sat[r1, c0] + sat[r0, c0]
    data = _data_blocks(item)
    return int(((n > 0) & data).sum()), int(n[data].sum())


def bbox_blocks(item: dict, polygon) -> int:
    """Data blocks of *item* that overlap the polygon bounds (the raw Subset window)."""
    crs = crs_of(item)
    x0, y0, x1, y1 = Transformer.from_crs(4326, crs, always_xy=True).transform_bounds(*polygon.bounds, densify_pts=21)
    r0, r1, c0, c1 = _block_ranges(item, crs, Affine(1, 0, x0, 0, -1, y1), 1)  # row/col in metres from the bbox corner
    inside = (r1 > 0) & (r0 < y1 - y0) & (c1 > 0) & (c0 < x1 - x0)
    return int((inside & _data_blocks(item)).sum())


def main(aoi_path: str, start: str, end: str, orbits: list[int]) -> None:
    import s1_orbits_for_aoi as o

    o.PERIOD = f"{start}T00:00:00Z/{end}T23:59:59Z"
    aoi_gdf = gpd.read_file(Path(aoi_path).expanduser())
    aoi = aoi_gdf.to_crs(METRIC_CRS).geometry.iloc[0]
    items = [f for f in search(aoi_gdf.geometry.iloc[0]) if f["properties"]["sat:relative_orbit"] in orbits
             and f["properties"]["sat:orbit_state"] == "descending"]
    x0, y0, x1, y1 = (np.floor(np.array(aoi.bounds) / CELL) * CELL).tolist()
    shape_ = (int((aoi.bounds[3] - y0) // CELL) + 1, int((aoi.bounds[2] - x0) // CELL) + 1)
    transform = from_origin(x0, aoi.bounds[3] + CELL, CELL, CELL)
    poly = features.rasterize([(aoi, 1)], shape_, transform=transform, all_touched=True).astype(bool)
    chunks256 = _pool(poly, 4)
    poly_px = aoi.area / PX

    print(f"AOI {aoi.area / 1e6:.0f} km2, {poly_px / 1e6:.0f} Mpx; {len(items)} slices, orbits {orbits}")
    total = dict.fromkeys(("raw", "peak"), 0.0)
    rows = []
    for orbit in orbits:
        sel = [f for f in items if f["properties"]["sat:relative_orbit"] == orbit]
        dates = {f["properties"]["datetime"][:10] for f in sel}
        fps = [features.rasterize([(gpd.GeoSeries([shape(f["geometry"])], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0], 1)],
                                  shape_, transform=transform).astype(bool) for f in sel]
        raw_px = sum(fp.sum() for fp in fps) * (CELL / 10) ** 2
        union = np.any(fps, axis=0)
        orbit_poly_px = (union & poly).sum() * (CELL / 10) ** 2  # cell resolution, polygon-sized
        orbit_poly_px = min(orbit_poly_px, poly_px)
        stack_px = (union & chunks256).sum() * (CELL / 10) ** 2
        n = len(dates)
        raw = raw_px * RAW_B_PX / 1e9
        stack = stack_px * n * STACK_B_PX_DATE / 1e9
        cube = stack_px * n * CUBE_B_PX_DATE / 1e9
        filtered = orbit_poly_px * n * FILTERED_B_PX_DATE / 1e9
        rows.append((orbit, n, len(sel), raw, stack, cube, filtered))
    print("orbit dates slices  raw_GB stack_GB cube_GB filtered_GB   (stack = float32, temporary)")
    for r in rows:
        print(f"{r[0]:>5} {r[1]:>5} {r[2]:>6} {r[3]:>7.0f} {r[4]:>8.0f} {r[5]:>7.0f} {r[6]:>11.0f}")
    raw_all = sum(r[3] for r in rows)
    # orbit by orbit, intermediates deleted: raw stays, one stack + Cube (then Filtered Cube) at a time
    peak = raw_all + max(r[4] + r[5] for r in rows)
    print(f"raw total {raw_all:.0f} GB; peak while stacking {peak:.0f} GB")

    polygon = aoi_gdf.to_crs(4326).geometry.iloc[0]
    gb = lambda blocks: blocks * 2 * BLOCK_B / 1e9  # noqa: E731
    windows = {}
    print(f"COG blocks fetched (vv and vh each; GB for both at {BLOCK_B / 1e3:.0f} kB per block), "
          f"odc-stac at {BLOCK} px chunks, margin {MARGIN} px")
    print("orbit crs        window_chunks  raw_blocks raw_GB  odc_unique unique_GB  odc_fetches fetch_GB")
    for orbit in orbits:
        sel = [f for f in items if f["properties"]["sat:relative_orbit"] == orbit]
        crs = Counter(crs_of(f) for f in sel).most_common(1)[0][0]  # odc-stac loads one orbit in one CRS
        if crs not in windows:
            t, shape_yx = cube_grid(polygon, crs)
            windows[crs] = t, chunk_window(polygon, crs, t, shape_yx, BLOCK, MARGIN)
        t, window = windows[crs]
        unique, fetches = (sum(v) for v in zip(*(window_blocks(f, crs, t, window) for f in sel)))
        raw_blocks = sum(bbox_blocks(f, polygon) for f in sel)
        print(f"{orbit:>5} {crs:<10} {window.sum():>7}/{window.size:<6} {raw_blocks:>10} {gb(raw_blocks):>6.0f} "
              f"{unique:>11} {gb(unique):>9.0f} {fetches:>12} {gb(fetches):>8.0f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], [int(a) for a in sys.argv[4:]])
