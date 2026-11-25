"""The load stage on synthetic COGs with file:// Items, against the stack stage on the same files."""

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pystac
import pytest
import rasterio
import xarray as xr
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform

from flood_monitoring import load, stack
from flood_monitoring.config import EventConfig, Processing, Series, Window
from flood_monitoring.paths import EventPaths

CRS = "EPSG:32630"
X0, Y0 = 500_000.0, 5_000_000.0  # top left of the slices, on the 10 m lattice
HEIGHT, WIDTH = 200, 240
NODATA = -32768.0
DAY = timedelta(days=1)
to_4326 = Transformer.from_crs(CRS, 4326, always_xy=True).transform


def write_cog(path, row0, rows, seed, holes=False):
    """A float32 power COG of *rows* lines starting at line *row0* of the slice grid."""
    rng = np.random.default_rng(seed)
    power = rng.uniform(0.005, 0.4, (rows, WIDTH)).astype("float32")
    power[:5, :5] = NODATA
    if holes:  # nodata where the next frame of the pass also has data: the merge must take the next frame's
        power[-20:, :50] = NODATA
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(driver="GTiff", height=rows, width=WIDTH, count=1, dtype="float32", crs=CRS, nodata=NODATA,
                   transform=from_origin(X0, Y0 - 10 * row0, 10, 10), tiled=True, blockxsize=32, blockysize=32)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(power, 1)


def make_item(raw, start, row0, rows, seed, holes=False):
    """An S1 RTC like Item over lines row0:row0+rows, its vv and vh written as Subsets under *raw*."""
    token = start.strftime("%Y%m%dT%H%M%S")
    item_id = f"S1A_IW_GRDH_1SDV_{token}_{(start + timedelta(seconds=25)).strftime('%Y%m%dT%H%M%S')}_000001_0000AA_rtc"
    footprint = shapely_transform(to_4326, box(X0, Y0 - 10 * (row0 + rows), X0 + 10 * WIDTH, Y0 - 10 * row0))
    item = pystac.Item(
        item_id, mapping(footprint), list(footprint.bounds), start + timedelta(seconds=12),
        {
            "start_datetime": start.isoformat().replace("+00:00", "Z"),
            "proj:code": CRS,
            "proj:transform": [10.0, 0.0, X0, 0.0, -10.0, Y0 - 10 * row0],
            "proj:shape": [rows, WIDTH],
        },
    )
    for k, asset in enumerate(("vv", "vh")):
        tif = raw / "sentinel-1-rtc" / start.date().isoformat() / item_id / f"{item_id}_{asset}_sub-test.tif"
        write_cog(tif, row0, rows, seed * 2 + k, holes)
        # the Planetary Computer gives vh the "overview" role too, which odc-stac skips unless told otherwise
        roles = ["data", "overview"] if asset == "vh" else ["data"]
        item.add_asset(asset, pystac.Asset(tif.as_uri(), media_type=pystac.MediaType.COG, roles=roles,
                                           extra_fields={"raster:bands": [{"nodata": NODATA, "data_type": "float32"}]}))
    return item


@pytest.fixture
def series(tmp_path):
    """Three passes: one full slice; two frames of one pass that overlap; one slice covering the top only."""
    raw = tmp_path / "raw" / "ev"
    t0 = datetime(2026, 1, 1, 6, 0, 0, 123456, tzinfo=timezone.utc)
    items = [
        make_item(raw, t0, 0, HEIGHT, 1),
        make_item(raw, t0 + 6 * DAY, 0, 120, 2, holes=True),
        make_item(raw, t0 + 6 * DAY + timedelta(seconds=25), 100, 100, 3),
        make_item(raw, t0 + 12 * DAY, 0, 90, 4),
    ]
    bbox = shapely_transform(to_4326, box(X0, Y0 - 10 * HEIGHT, X0 + 10 * WIDTH, Y0)).bounds
    config = EventConfig(
        project="flood-monitoring", event="ev", area_of_interest=bbox,
        window=Window(date(2026, 1, 1), date(2026, 1, 31)),
        series=Series("sentinel-1-rtc", date(2026, 1, 1), date(2026, 1, 31), "descending", "IW", (8,), ("vv", "vh")),
        processing=Processing(memory_budget_mb=512),
    )
    return config, items


@pytest.fixture
def small_blocks(monkeypatch):
    """COG blocks of 64 px instead of 512, so the small grid holds several chunks."""
    monkeypatch.setattr(load, "COG_BLOCK", 64)


def run_load(config, items, data_root, monkeypatch):
    monkeypatch.setattr(load, "search", lambda config: items)
    paths = EventPaths("ev", data_root)
    load.run(config, paths)
    return paths


def test_load_cube_equals_the_stack_cube_on_the_same_files(series, tmp_path, monkeypatch, small_blocks):
    config, items = series
    stacked = EventPaths("ev", tmp_path)
    stack.run(config, stacked)
    loaded = run_load(config, items, tmp_path / "load", monkeypatch)

    with xr.open_dataset(stacked.cube) as ref, xr.open_dataset(loaded.cube) as cand:
        # one step per pass, stamped with the earliest acquisition start of its day, to the second
        assert list(cand.time.values) == list(ref.time.values)
        assert str(cand.time.values[1]) == "2026-01-07T06:00:00.000000000"
        # odc-stac snaps the bbox outward: the stack grid is a part of the load grid on the same lattice
        part = cand.sel(x=ref.x, y=ref.y)
        assert part.sizes == ref.sizes
        for name in ("VV", "VH"):
            assert cand[name].encoding["dtype"] == "int16"
            np.testing.assert_array_equal(part[name].values, ref[name].values)
            rim = cand[name].where(~cand.x.isin(ref.x) | ~cand.y.isin(ref.y))
            assert np.isnan(rim.values).all()


def test_chunks_outside_the_polygon_window_read_back_as_nodata(series, tmp_path, monkeypatch, small_blocks):
    config, items = series
    full = run_load(config, items, tmp_path / "full", monkeypatch)
    # a polygon over the top left corner: with the despeckle halo it touches the first chunk only
    corner = shapely_transform(to_4326, box(X0 + 100, Y0 - 300, X0 + 300, Y0 - 100))
    windowed = run_load(replace(config, aoi_polygon=corner), items, tmp_path / "windowed", monkeypatch)

    with np.load(windowed.window) as saved:
        side, window = int(saved["side"]), saved["window"]
    assert side % 64 == 0 and window.shape == (2, 2) and window.sum() == 1 and window[0, 0]
    with xr.open_dataset(full.cube) as ref, xr.open_dataset(windowed.cube) as cand:
        for name in ("VV", "VH"):
            np.testing.assert_array_equal(cand[name].values[:, :side, :side], ref[name].values[:, :side, :side])
            outside = cand[name].values.copy()
            outside[:, :side, :side] = np.nan
            assert np.isnan(outside).all()
            assert np.isfinite(ref[name].values[:, side:, side:]).any()  # there was data to skip


def failing_reads(monkeypatch, failures):
    """odc-stac's load, failing *failures* times on the chunk at the grid's origin; returns the calls per chunk."""
    real, calls = load.odc.stac.load, Counter()

    def flaky(items, **kw):
        if "geobox" in kw:
            key = (kw["geobox"].transform.c, kw["geobox"].transform.f)
            calls[key] += 1
            if key == min(calls) and calls[key] <= failures:
                raise OSError("HTTP 503")
        return real(items, **kw)

    monkeypatch.setattr(load.odc.stac, "load", flaky)
    monkeypatch.setattr(load, "RETRY_DELAY_S", 0.0)
    return calls


def test_a_chunk_that_fails_twice_is_retried_and_the_cube_is_complete(series, tmp_path, monkeypatch, small_blocks):
    config, items = series
    reference = run_load(config, items, tmp_path / "ref", monkeypatch)
    calls = failing_reads(monkeypatch, failures=2)
    flaky = run_load(config, items, tmp_path / "flaky", monkeypatch)

    assert sorted(calls.values())[-1] == 3  # two failures, then the read that succeeded
    with xr.open_dataset(reference.cube) as ref, xr.open_dataset(flaky.cube) as cand:
        xr.testing.assert_identical(cand, ref)


def test_a_chunk_that_keeps_failing_fails_the_stage_and_leaves_no_cube(series, tmp_path, monkeypatch, small_blocks):
    config, items = series
    failing_reads(monkeypatch, failures=load.RETRIES + 1)
    with pytest.raises(RuntimeError, match="failed 4 times"):
        run_load(config, items, tmp_path, monkeypatch)
    assert not EventPaths("ev", tmp_path).cube.exists()
