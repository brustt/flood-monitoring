"""Prototype: build the Cube straight from the STAC COGs with odc-stac (no raw download, no float32 stack) and
compare it with the stack stage's Cube.

The odc-stac Cube goes through the same writer as the stack stage (``cube.write_db_cube``), so with a polygon only
the chunks of ``aoi.chunk_window`` are read over the network, computed and stored.

Usage, from the repo root (odc-stac and pystac-client are not project dependencies):
  RUN="uv run --with odc-stac --with pystac-client python prototypes/odc_stac_cube.py"
  $RUN reference <data_root> <event>              # stack stage on <data_root>/raw/<event> -> <data_root>/interim/<event>/cube.nc
  $RUN odc <out.nc> <event> [--polygon <file>]    # odc-stac Cube; --polygon overrides the Event's Area of Interest
  $RUN compare <reference.nc> <candidate.nc>

Results on saintes-2026-02 (31 Acquisitions, 1745 x 1285 px, one frame per date), 2026-11-06, ~1.4 MB/s link:
  - Values: bit-identical to the stack stage's Cube (VV and VH, every pixel and date, same nodata), same grid,
    same file size. Only the time stamps differ: odc-stac uses the item datetime (scene centre), eo-pipe the
    acquisition start; the days are the same.
  - Time: eo-stac download 315 s + stack 27 s = 342 s and 631 MB of raw Subsets left on disk; odc-stac 393 s at
    512 px chunks (the COG block size), 869 s at the planned 256 px (each 512 px block fetched up to 4 times), no
    raw files. Both are network-bound. write_db_cube reads each chunk's dates one after another
    (scheduler="synchronous"), which is fine on local files but serialises the HTTP reads.
  - Polygon (Charente 2 km buffer clipped to the Saintes bbox, 69 km2): grid shrinks to the polygon bounds and
    8/9 chunks of 512 px are read and stored, 278 s, 117 MB; stored pixels identical, the skipped chunk is never
    fetched.
  - The despeckle stage runs unchanged on the odc-stac Cube.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import odc.stac
import planetary_computer
import pystac_client
import xarray as xr

from flood_monitoring import aoi, chunks, stack
from flood_monitoring.config import _read_polygon, load_event_config
from flood_monitoring.cube import check_cube, write_db_cube
from flood_monitoring.detection import to_db
from flood_monitoring.download import STAC_API
from flood_monitoring.paths import EventPaths

# A failed read must fail the run (odc-stac's fail_on_error stays True), so retry transient HTTP errors first.
GDAL_ENV = {
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
}


def reference(data_root: Path, event: str) -> None:
    config = load_event_config(event)
    start = time.perf_counter()
    stack.run(config, EventPaths(event, data_root), force=True)
    print(f"reference: {time.perf_counter() - start:.1f} s")


def search(config, polygon) -> list:
    series = config.series
    client = pystac_client.Client.open(STAC_API, modifier=planetary_computer.sign_inplace)
    items = client.search(
        collections=[series.collection],
        intersects=polygon if polygon is not None else None,
        bbox=None if polygon is not None else list(config.area_of_interest),
        datetime=f"{series.start.isoformat()}/{series.end.isoformat()}",
        query={
            "sat:orbit_state": {"eq": series.orbit_state},
            "sar:instrument_mode": {"eq": series.instrument_mode},
            "sat:relative_orbit": {"eq": series.relative_orbits[0]},
        },
    ).item_collection()
    return list(items)


def build(out: Path, event: str, polygon_file: Path | None, side: int | None = None) -> None:
    config = load_event_config(event)
    polygon = _read_polygon(polygon_file) if polygon_file else config.aoi_polygon
    bbox = list(polygon.bounds) if polygon is not None else list(config.area_of_interest)
    budget = config.processing.memory_budget_mb
    assets = list(config.series.assets)

    start = time.perf_counter()
    items = search(config, polygon)
    crs = items[0].properties["proj:code"]  # one orbit: the native UTM zone of its frames
    print(f"{len(items)} items, {crs}, search {time.perf_counter() - start:.1f} s")

    # Native lattice: the S1 RTC transforms sit on multiples of 10 m, which odc-stac snaps the bbox to.
    lazy = odc.stac.load(items, bands=assets, crs=crs, resolution=10, bbox=bbox, groupby="solar_day", chunks={})
    dates, height, width = lazy.sizes["time"], lazy.sizes["y"], lazy.sizes["x"]
    plan = chunks.plan(budget, stack.BYTES_PER_DATE_VAR * len(assets) * dates, height, width)
    # The S1 RTC COGs have 512 px blocks: a smaller chunk fetches each block several times
    side = side or plan.side
    ds = odc.stac.load(
        items, bands=assets, crs=crs, resolution=10, bbox=bbox, groupby="solar_day",
        chunks={"time": 1, "y": side, "x": side}, patch_url=planetary_computer.sign,
    ).chunk({"time": -1})  # write_db_cube wants the full time axis per spatial chunk
    ds = ds.rename({a: a.upper() for a in assets})

    window = None
    if polygon is not None:
        settings = config.despeckle
        margin = max(1, settings.lee_window // 2 + settings.quegan_window // 2)
        window = aoi.chunk_window(polygon, ds.odc.crs, ds.odc.transform, (height, width), side, margin)
        print(f"window: {window.sum()}/{window.size} chunks of {side} px touch the polygon ({window.mean():.1%})")

    db = xr.Dataset({n: v.copy(data=v.data.map_blocks(to_db, dtype="float32")) for n, v in ds.data_vars.items()})
    workers = plan.workers
    odc.stac.configure_rio(cloud_defaults=True, **GDAL_ENV)  # applied in each reading thread
    with chunks.scheduler(workers, budget):
        stored = write_db_cube(db, out, window, workers)
    print(f"odc cube: {stored}/{window.size if window is not None else stored} chunks, "
          f"{out.stat().st_size / 2**20:.1f} MB, {time.perf_counter() - start:.1f} s")

    cube, plan = chunks.open_cube(out, budget, bytes_per_date=10)
    with cube, chunks.scheduler(plan.workers, budget):
        check_cube(cube, nodata=None, expected=len({i.datetime.date() for i in items}))
    print(f"odc cube: check_cube passed ({dates} Acquisitions)")


def compare(ref_path: Path, cand_path: Path) -> None:
    with xr.open_dataset(ref_path) as ref, xr.open_dataset(cand_path) as cand:
        print(f"size  ref {dict(ref.sizes)}  cand {dict(cand.sizes)}")
        print(f"file  ref {ref_path.stat().st_size / 2**20:.1f} MB  cand {cand_path.stat().st_size / 2**20:.1f} MB")
        print(f"x0/y0 ref {ref.x.values[0]}/{ref.y.values[0]}  cand {cand.x.values[0]}/{cand.y.values[0]}")
        print(f"crs   ref {ref.rio.crs}  cand {cand.rio.crs}")
        same_days = np.array_equal(ref.time.values.astype("datetime64[D]"), cand.time.values.astype("datetime64[D]"))
        print(f"time  same days: {same_days}; first ref {ref.time.values[0]} cand {cand.time.values[0]}")
        # eo-pipe stamps the acquisition start, odc-stac the item datetime (scene centre): match on the day
        day = lambda ds: ds.assign_coords(time=ds.time.values.astype("datetime64[D]"))  # noqa: E731
        r, c = xr.align(day(ref), day(cand), join="inner")
        print(f"common grid {dict(r.sizes)}")
        for name in ref.data_vars:
            a, b = r[name].values, c[name].values
            both = ~np.isnan(a) & ~np.isnan(b)
            only_ref, only_cand = int((~np.isnan(a) & np.isnan(b)).sum()), int((np.isnan(a) & ~np.isnan(b)).sum())
            diff = np.abs(a[both] - b[both])
            print(f"{name}: {both.sum()} px valid in both, max |diff| {diff.max():.4f} dB, "
                  f"equal {np.mean(diff == 0):.4%}; valid only in ref {only_ref}, only in cand {only_cand}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("reference")
    p.add_argument("data_root", type=Path)
    p.add_argument("event")
    p = sub.add_parser("odc")
    p.add_argument("out", type=Path)
    p.add_argument("event")
    p.add_argument("--polygon", type=Path)
    p.add_argument("--side", type=int, help="chunk side in px (default: planned from the memory budget)")
    p = sub.add_parser("compare")
    p.add_argument("reference", type=Path)
    p.add_argument("candidate", type=Path)
    args = parser.parse_args()
    if args.cmd == "reference":
        reference(args.data_root, args.event)
    elif args.cmd == "odc":
        build(args.out, args.event, args.polygon, args.side)
    else:
        compare(args.reference, args.candidate)


if __name__ == "__main__":
    main()
