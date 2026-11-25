"""Build the Cube straight from the STAC COGs with odc-stac: no raw Subsets, no float32 Tile stack.

Writes the same Cube as the stack stage (``paths.cube``, int16 dB, the chunks of the Area of Interest window). Each
chunk is read on its own: one odc-stac load of that chunk's grid, with its dates fetched by ``io_threads`` threads,
the URLs signed at that moment (so a run of many hours never uses an expired token) and retried on failure.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable

import dask.array as da
import numpy as np
import odc.stac
import planetary_computer
import psutil
import pystac
import pystac_client
import xarray as xr
from eo_pipe.io.memory import workers_for
from shapely.geometry import mapping
from stac_geoparquet.arrow import parse_stac_items_to_arrow, to_parquet

from . import aoi, chunks
from .config import EventConfig
from .cube import check_cube, write_db_cube
from .detection import to_db
from .download import STAC_API
from .paths import EventPaths
from .stack import BYTES_PER_DATE_VAR, halo_margin

COG_BLOCK = 512  # block side of the sentinel-1-rtc COGs: a smaller chunk fetches each block several times
RESOLUTION = 10.0  # metres, the native pixel of the RTC COGs
RETRIES = 3  # per chunk, before the stage fails
RETRY_DELAY_S = 10.0  # before the first retry; doubled at each one
PROGRESS_EVERY_S = 30.0
# Chunks read at once, each with io_threads reads in flight. On Saintes (home link, ~5 MB/s) 1 chunk x 8 reads ran at
# 5.2 MB/s, 9 chunks x 8 reads at 2 MB/s with read errors: the link saturates and more requests only queue. The
# second chunk keeps the link busy while the first is converted and written.
CHUNKS_IN_FLIGHT = 2
# odc-stac fails the load on a read error, so transient HTTP errors are retried by GDAL first. Azure Blob speaks
# HTTP/1.1 only (#42), so the HTTP/2 settings are harmless there and help a server that multiplexes.
GDAL_ENV = {
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
}


def search(config: EventConfig) -> list[pystac.Item]:
    """Items of the Series (one relative orbit), unsigned: each chunk read signs its own URLs."""
    series = config.series
    (orbit,) = series.relative_orbits
    polygon = config.aoi_polygon
    found = pystac_client.Client.open(STAC_API).search(
        collections=[series.collection],
        intersects=mapping(polygon) if polygon is not None else None,
        bbox=None if polygon is not None else list(config.area_of_interest),
        datetime=f"{series.start.isoformat()}/{series.end.isoformat()}",
        query={
            "sat:orbit_state": {"eq": series.orbit_state},
            "sar:instrument_mode": {"eq": series.instrument_mode},
            "sat:relative_orbit": {"eq": orbit},
        },
    )
    return list(found.item_collection())


def acquisition_starts(items: list[pystac.Item], times: np.ndarray) -> np.ndarray:
    """Each step of *times* (odc-stac's, one per solar day) restamped with the earliest ``start_datetime`` of its
    items, to the second: eo-pipe's stamp, the acquisition start in the Item id."""
    times = np.asarray(times, dtype="datetime64[ns]")
    starts = np.full(len(times), np.datetime64("NaT", "s"), dtype="datetime64[s]")
    for item in items:
        # odc-stac stamps a step with the earliest Item datetime of its day; the steps are days apart
        step = np.searchsorted(times, np.datetime64(item.datetime.replace(tzinfo=None), "ns"), side="right") - 1
        start = np.datetime64(item.properties["start_datetime"].rstrip("Z"), "s")
        if np.isnat(starts[step]) or start < starts[step]:
            starts[step] = start
    return starts.astype("datetime64[ns]")


def without_overview_role(items: list[pystac.Item], assets: list[str]) -> list[pystac.Item]:
    """*items* with the "overview" role dropped from *assets*: the Planetary Computer gives it to the full-resolution
    vh COG, and odc-stac then leaves vh out of the bands it can load."""
    items = [item.clone() for item in items]
    for item in items:
        for name in assets:
            roles = item.assets[name].roles
            if roles and "overview" in roles:
                item.assets[name].roles = [r for r in roles if r != "overview"]
    return items


def _crs(items: list[pystac.Item]) -> str:
    """The native CRS of most Items of the orbit; the others are reprojected to it."""
    return Counter(item.properties["proj:code"] for item in items).most_common(1)[0][0]


def _template(lazy: xr.Dataset, assets: list[str], times: np.ndarray, side: int) -> xr.Dataset:
    """The Cube's dB float32 layout, chunked (all dates, side, side), on the grid of *lazy*; never computed."""
    shape = (len(times), lazy.sizes["y"], lazy.sizes["x"])
    blank = da.zeros(shape, chunks=(-1, side, side), dtype="float32")
    coords = {"time": times, "y": lazy["y"], "x": lazy["x"], "spatial_ref": lazy["spatial_ref"]}
    return xr.Dataset({a.upper(): (("time", "y", "x"), blank) for a in assets}, coords=coords)


class _Progress:
    """Chunks done of *total*, MB received by the host since the start, MB/s and time left, every PROGRESS_EVERY_S."""

    def __init__(self, total: int):
        self.total, self.done = total, 0
        self.start = self.last = time.monotonic()
        self.received = psutil.net_io_counters().bytes_recv
        self.lock = threading.Lock()

    def chunk_done(self) -> None:
        with self.lock:
            self.done += 1
            now = time.monotonic()
            if now - self.last >= PROGRESS_EVERY_S or self.done == self.total:
                self.last = now
                self.report(now)

    def report(self, now: float) -> None:
        elapsed = now - self.start
        mb = (psutil.net_io_counters().bytes_recv - self.received) / 1e6
        left = elapsed / self.done * (self.total - self.done)
        print(f"load: {self.done}/{self.total} chunks, {mb:.0f} MB received, {mb / max(elapsed, 1e-9):.2f} MB/s, "
              f"{elapsed / 60:.1f} min, {left / 60:.1f} min left", flush=True)


def _with_retries(read: Callable[[slice, slice], list[np.ndarray]], y: slice, x: slice) -> list[np.ndarray]:
    """``read(y, x)``, tried again RETRIES times after RETRY_DELAY_S, doubled each time; the last failure raises."""
    where = f"chunk at rows {y.start}:{y.stop}, columns {x.start}:{x.stop}"
    for attempt in range(RETRIES + 1):
        try:
            return read(y, x)
        except Exception as exc:  # any read error: HTTP, an expired signature, GDAL
            if attempt == RETRIES:
                raise RuntimeError(f"{where} failed {RETRIES + 1} times: {exc}") from exc
            delay = RETRY_DELAY_S * 2**attempt
            print(f"load: {where} failed ({exc}); retry {attempt + 1}/{RETRIES} in {delay:.0f} s", flush=True)
            time.sleep(delay)


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.cube.exists() and not force:
        print(f"skip: {paths.cube} exists")
        return
    items = search(config)
    if not items:
        raise RuntimeError(f"no {config.series.collection} items found for Event {config.event!r}")
    paths.interim.mkdir(parents=True, exist_ok=True)
    to_parquet(parse_stac_items_to_arrow(items), paths.items)

    assets = list(config.series.assets)
    items = without_overview_role(items, assets)
    budget = config.processing.memory_budget_mb
    load_args = dict(bands=assets, groupby="solar_day", fail_on_error=True)
    # No pixel is read here: the grid and the dates only. The S1 RTC transforms sit on multiples of 10 m, which
    # odc-stac snaps the bbox to, so the grid is the native lattice.
    crs = _crs(items)
    lazy = odc.stac.load(items, crs=crs, resolution=RESOLUTION, bbox=config.area_of_interest, chunks={}, **load_args)
    geobox, times = lazy.odc.geobox, acquisition_starts(items, lazy["time"].values)
    height, width = geobox.shape
    print(f"{len(items)} items, {len(times)} Acquisitions, {height} x {width} px, {crs}")

    bytes_per_pixel = BYTES_PER_DATE_VAR * len(assets) * len(times)
    side = max(chunks.plan(budget, bytes_per_pixel, height, width, stored=COG_BLOCK).side, COG_BLOCK)
    workers = min(workers_for(budget - chunks.reserved_mb(budget, height, width), bytes_per_pixel, side),
                  CHUNKS_IN_FLIGHT)
    print(f"chunks: {side} px, {workers} at a time, {config.processing.io_threads} reads each")

    window = None
    paths.window.unlink(missing_ok=True)
    if config.aoi_polygon is not None:
        # Only the chunks that touch the polygon are read and written; the others read back as nodata.
        window = aoi.chunk_window(config.aoi_polygon, geobox.crs, geobox.transform, (height, width), side,
                                  halo_margin(config))
        np.savez(paths.window, window=window, side=side)
        print(f"window: {window.sum()}/{window.size} chunks of {side} px touch the polygon ({window.mean():.1%})")

    def read(y: slice, x: slice) -> list[np.ndarray]:
        ds = odc.stac.load(items, geobox=geobox[y, x], patch_url=planetary_computer.sign,
                           pool=config.processing.io_threads, **load_args)
        if ds.sizes["time"] != len(times):
            raise RuntimeError(f"chunk has {ds.sizes['time']} dates, the grid {len(times)}")
        return [to_db(ds[a].values) for a in assets]

    template = _template(lazy, assets, times, side)
    grid = len(template.chunks["y"]) * len(template.chunks["x"])
    progress = _Progress(grid if window is None else int(window.sum()))

    def read_chunk(y: slice, x: slice) -> list[np.ndarray]:
        blocks = _with_retries(read, y, x)
        progress.chunk_done()
        return blocks

    odc.stac.configure_rio(cloud_defaults=True, **GDAL_ENV)  # applied in each reading thread
    with chunks.scheduler(workers, budget):
        stored = write_db_cube(template, paths.cube, window, workers, read=read_chunk)
    print(f"cube: {stored}/{grid} chunks stored, "
          f"{paths.cube.stat().st_size / 2**30:.2f} GB")

    ds, plan = chunks.open_cube(paths.cube, budget, bytes_per_date=10)
    with ds, chunks.scheduler(plan.workers, budget):
        check_cube(ds, nodata=None, expected=len(times))
        print(f"cube: {paths.cube} ({ds.sizes['time']} Acquisitions, variables {list(ds.data_vars)})")
