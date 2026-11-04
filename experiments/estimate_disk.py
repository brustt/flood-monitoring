"""Estimate the disk a Series needs, per relative orbit, from the STAC footprints and the AOI polygon.

Sizes follow what the pipeline stores (measured on charente-2026-02, issue #39):
  raw       Subsets are the slice cut to the AOI bbox (measured: the blocks outside the polygon are not skipped):
            9.4 B per pixel (vv + vh, DEFLATE float32)
  stack     float32 stack of the 256 px chunks that touch the polygon, 8 B per pixel and date (peak only)
  Cube      int16 dB, zlib: 2.4 B per pixel of the 256 px chunks touched, per date (1.93 GB / (22.7 Mpx x 35 dates))
  Filtered  int16 dB, zlib: 2.8 B per polygon pixel and date (1.32 GB / (13.3 Mpx x 35 dates))
The footprints include nodata corners, so the raw figure is slightly high.

Usage: uv run --no-project --with geopandas --with requests --with rasterio python experiments/estimate_disk.py \
           <aoi.gpkg> <start> <end> <orbit> [<orbit> ...]
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).parent))
from s1_orbits_for_aoi import METRIC_CRS, search  # noqa: E402

CELL = 640.0  # m, one 64 px chunk of the Filtered Cube; 256 px chunks = 4 cells
RAW_B_PX, STACK_B_PX_DATE, CUBE_B_PX_DATE, FILTERED_B_PX_DATE = 9.4, 8.0, 2.4, 2.8
PX = 100.0  # m2 per 10 m pixel


def _pool(mask: np.ndarray, k: int) -> np.ndarray:
    """True for every cell of a k x k group that holds a True cell."""
    h, w = (-(-s // k) * k for s in mask.shape)
    padded = np.zeros((h, w), bool)
    padded[: mask.shape[0], : mask.shape[1]] = mask
    pooled = padded.reshape(h // k, k, w // k, k).any(axis=(1, 3))
    return np.kron(pooled, np.ones((k, k), bool))[: mask.shape[0], : mask.shape[1]]


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


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], [int(a) for a in sys.argv[4:]])
