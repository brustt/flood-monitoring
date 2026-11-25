"""Read throughput of the Planetary Computer sentinel-1-rtc COGs at several concurrencies (issue #42).

Reads a fixed set of 512 px COG blocks (vv and vh of orbit 8, blocks inside the slice footprint and touching the AOI)
with rasterio, opening the COG once per block as odc-stac does once per chunk read. Each concurrency runs in a fresh
process, so no GDAL cache carries over; the first level is run again at the end to check that the server did not
cache the blocks. MB/s counts the compressed block bytes only (TIFF tile byte counts), not the headers.
GDAL settings are those of prototypes/odc_stac_cube.py (which override odc-stac's cloud defaults) plus HTTP/2
multiplexing. The reference is one plain single-stream download of a COG with curl.

Usage: uv run python experiments/pc_throughput.py [--items 8] [--blocks 8] [--levels 1 4 8 16 32] [--curl-mb 200]
"""
import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import geopandas as gpd
import numpy as np
import planetary_computer
import rasterio
from rasterio.windows import Window
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
from s1_orbits_for_aoi import AOI, crs_of, search  # noqa: E402

ORBIT, BLOCK = 8, 512
GDAL_ENV = {
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
}


def data_blocks(item: dict, aoi: gpd.GeoSeries) -> list[tuple[int, int]]:
    """(row, col) of the item's blocks that lie inside its footprint and touch the AOI."""
    crs = crs_of(item)
    footprint = gpd.GeoSeries([shape(item["geometry"])], crs="EPSG:4326").to_crs(crs).iloc[0]
    aoi_ = aoi.to_crs(crs).iloc[0]
    a, _, x0, _, e, y0 = item["properties"]["proj:transform"][:6]
    h, w = item["properties"]["proj:shape"]
    out = []
    for r in range(h // BLOCK):  # full blocks only
        for c in range(w // BLOCK):
            b = box(x0 + c * BLOCK * a, y0 + (r + 1) * BLOCK * e, x0 + (c + 1) * BLOCK * a, y0 + r * BLOCK * e)
            if footprint.contains(b) and aoi_.intersects(b):
                out.append((r, c))
    return out


def plan(n_items: int, n_blocks: int) -> list[tuple[str, int, int, int]]:
    """Fixed sample: (signed href, row, col, compressed bytes) for vv and vh of *n_blocks* blocks of *n_items* items."""
    aoi = gpd.read_file(AOI).geometry
    items = sorted((f for f in search(aoi.iloc[0]) if f["properties"]["sat:relative_orbit"] == ORBIT
                    and f["properties"]["sat:orbit_state"] == "descending"), key=lambda f: f["id"])
    rng = np.random.default_rng(0)
    out = []
    for item in items[:: max(1, len(items) // n_items)][:n_items]:
        blocks = data_blocks(item, aoi)
        picks = [blocks[i] for i in rng.choice(len(blocks), min(n_blocks, len(blocks)), replace=False)]
        for asset in ("vv", "vh"):
            href = planetary_computer.sign(item["assets"][asset]["href"])
            with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
                out += [(href, r, c, int(ds.get_tag_item(f"BLOCK_SIZE_{c}_{r}", "TIFF", bidx=1))) for r, c in picks]
    return out


def _read(block: tuple[str, int, int, int]) -> None:
    href, r, c, _ = block
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        ds.read(1, window=Window(c * BLOCK, r * BLOCK, BLOCK, BLOCK))


def run_level(blocks: list, concurrency: int) -> float:
    """Wall seconds to read every block with *concurrency* threads (called in a fresh process)."""
    start = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(_read, blocks))
    return time.perf_counter() - start


def curl(href: str, n_bytes: int) -> tuple[float, str]:
    """MB/s and HTTP version of one plain download of the first *n_bytes* of *href*."""
    out = subprocess.run(["curl", "-s", "-o", os.devnull, "-r", f"0-{n_bytes - 1}", "-w",
                          "%{size_download} %{time_total} %{http_version}", href],
                         check=True, capture_output=True, text=True).stdout.split()
    return int(out[0]) / float(out[1]) / 1e6, out[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=8)
    parser.add_argument("--blocks", type=int, default=8, help="blocks per item; each is read in vv and vh")
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 4, 8, 16, 32])
    parser.add_argument("--curl-mb", type=int, default=200)
    args = parser.parse_args()

    blocks = plan(args.items, args.blocks)
    sizes = np.array([b[3] for b in blocks])
    mb = sizes.sum() / 1e6
    print(f"{len(blocks)} blocks of {BLOCK} px from {args.items} items of orbit {ORBIT}, {mb:.1f} MB compressed; "
          f"bytes per block mean {sizes.mean() / 1e3:.0f} kB, min {sizes.min() / 1e3:.0f}, max {sizes.max() / 1e3:.0f}")

    speed, version = curl(blocks[0][0], args.curl_mb * 10**6)
    print(f"curl, 1 stream, {args.curl_mb} MB: {speed:.2f} MB/s (HTTP {version})")
    print("concurrency  seconds   MB/s")
    for level in [*args.levels, args.levels[0]]:
        with ProcessPoolExecutor(1, mp_context=get_context("spawn")) as proc:
            seconds = proc.submit(run_level, blocks, level).result()
        print(f"{level:>11} {seconds:>8.1f} {mb / seconds:>6.2f}")
    speed, version = curl(blocks[-1][0], args.curl_mb * 10**6)
    print(f"curl, 1 stream, {args.curl_mb} MB, again: {speed:.2f} MB/s (HTTP {version})")


if __name__ == "__main__":
    main()
