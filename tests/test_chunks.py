import re
import warnings

import pytest
import rasterio.env
from eo_pipe.io.memory import ChunkPlan
from eo_pipe.pipeline import PipelineComposition

import reference_inputs as ri
from flood_monitoring import chunks, composite, despeckle, event, flood_extent, merge_stage, stack


def cube(tmp_path, size=48, dates=12, chunk=16):
    path = tmp_path / "cube.nc"
    ri.write_cube(path, size=size, dates=dates, chunk=chunk, flood_from=9, flood_fraction=0.5, salt_fraction=0.01)
    return path


def test_open_cube_keeps_time_in_one_chunk(tmp_path):
    ds, _ = chunks.open_cube(cube(tmp_path), 4096, bytes_per_date=40)
    with ds:
        assert ds["VV"].chunksizes["time"] == (12,)


def test_open_cube_spatial_chunks_follow_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(chunks, "plan", lambda *args, **kwargs: ChunkPlan(16, 2))
    ds, plan = chunks.open_cube(cube(tmp_path), 4096, bytes_per_date=40)
    with ds:
        assert plan == ChunkPlan(16, 2)
        assert ds["VV"].chunksizes["y"] == (16, 16, 16) and ds["VV"].chunksizes["x"] == (16, 16, 16)


def test_time_chunk_one_for_composite(tmp_path, monkeypatch):
    seen = []

    def plan(budget_mb, bytes_per_pixel, height, width, stored=chunks.TILE):
        seen.append(bytes_per_pixel)
        return ChunkPlan(16, 2)

    monkeypatch.setattr(chunks, "plan", plan)
    ds, _ = chunks.open_cube(cube(tmp_path), 4096, bytes_per_date=28, time_chunk=1)
    with ds:
        assert set(ds["VV"].chunksizes["time"]) == {1}
    assert seen == [28]


def test_plan_subtracts_both_gdal_caches_and_aoi_mask_and_prints_chunk_line(monkeypatch, capsys):
    budgets = []

    def plan_chunks(budget, bytes_per_pixel, height, width):
        budgets.append(budget)
        return ChunkPlan(512, 3)

    monkeypatch.setattr(chunks, "plan_chunks", plan_chunks)
    assert chunks.plan(1024, 400, 2048, 1024) == ChunkPlan(512, 3)
    assert budgets == [1024 - 2 * 64 - 2]  # two 64 MB caches, a 2 MB mask
    assert capsys.readouterr().out == "chunks: 512 px, 3 at a time\n"


@pytest.mark.parametrize(
    ("planned", "stored", "side"),
    [(700, 288, 576), (200, 288, 144), (17, 288, 16), (20, 25, 20), (300, chunks.TILE, 256)],
)
def test_plan_aligns_side_to_stored_chunks(monkeypatch, planned, stored, side):
    monkeypatch.setattr(chunks, "plan_chunks", lambda *args: ChunkPlan(planned, 2))
    assert chunks.plan(4096, 40, 1000, 1000, stored).side == side


def test_plan_default_stored_is_the_geotiff_tile(monkeypatch):
    monkeypatch.setattr(chunks, "plan_chunks", lambda *args: ChunkPlan(300, 2))
    assert chunks.plan(4096, 40, 1000, 1000).side == 256


def test_open_cube_reads_stored_chunk_side_and_emits_no_chunk_warning(tmp_path, monkeypatch):
    stored = []
    real = chunks.plan

    def plan(budget_mb, bytes_per_pixel, height, width, stored_side=chunks.TILE):
        stored.append(stored_side)
        return real(budget_mb, bytes_per_pixel, height, width, stored_side)

    monkeypatch.setattr(chunks, "plan", plan)
    path = cube(tmp_path, size=100, chunk=24)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ds, p = chunks.open_cube(path, 4096, bytes_per_date=40)
        with ds:
            ds["VV"].isel(time=0).values
    assert stored == [24] and p.side % 24 == 0


@pytest.mark.parametrize("time_chunk", [-1, 1])
def test_open_cube_splitting_stored_chunks_emits_no_warning(tmp_path, monkeypatch, time_chunk):
    monkeypatch.setattr(chunks, "plan", lambda *args, **kwargs: ChunkPlan(12, 2))  # divides the stored 24
    path = cube(tmp_path, size=100, chunk=24)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ds, _ = chunks.open_cube(path, 4096, bytes_per_date=40, time_chunk=time_chunk)
        with ds:
            assert ds["VV"].chunksizes["y"][0] == 12
            ds["VV"].isel(time=0).values


@pytest.mark.parametrize("bytes_per_pixel", [2400, 1e6])  # the minimum below and above the 32 MB cache floor
def test_plan_too_small_names_configured_budget_and_a_sufficient_one(bytes_per_pixel):
    with pytest.raises(ValueError, match=r"memory_budget_mb=1 is too small") as exc:
        chunks.plan(1, bytes_per_pixel, 1024, 1024)
    minimum = int(re.search(r"at least (\d+)$", str(exc.value)).group(1))
    for budget in (64, minimum - 1):
        if budget >= minimum:
            continue
        with pytest.raises(ValueError, match=rf"memory_budget_mb={budget} is too small"):
            chunks.plan(budget, bytes_per_pixel, 1024, 1024)
    chunks.plan(minimum, bytes_per_pixel, 1024, 1024)


def test_scheduler_bounds_both_gdal_caches_and_restores_osgeo():
    from osgeo import gdal

    before = gdal.GetCacheMax()
    with chunks.scheduler(2, 1024):
        assert gdal.GetCacheMax() == 64 * 2**20
        assert int(rasterio.env.getenv()["GDAL_CACHEMAX"]) == 64 * 2**20
    assert gdal.GetCacheMax() == before


class Stop(Exception):
    pass


def test_stack_and_despeckle_pass_budget_to_eo_pipe(tmp_path, monkeypatch):
    steps = {}

    def add_step(self, name, batch, **params):
        steps[name] = params
        return self

    def run(self, *args, **kwargs):
        raise Stop

    monkeypatch.setattr(PipelineComposition, "add_step", add_step)
    monkeypatch.setattr(PipelineComposition, "run", run)
    config, paths = ri.single_series_config(tmp_path, budget_mb=777)
    subset = paths.raw / "sentinel-1-rtc" / "2026-01-01" / "item" / "item_vv_sub-abc.tif"
    subset.parent.mkdir(parents=True)
    subset.touch()
    with pytest.raises(Stop):
        stack.run(config, paths)
    paths.interim.mkdir(parents=True)
    paths.cube.touch()
    with pytest.raises(Stop):
        despeckle.run(config, paths)
    assert {name: p["memory_budget_mb"] for name, p in steps.items()} == {
        "stack_tiles": 777, "lee_filter": 777, "quegan_filter": 777,
    }


@pytest.mark.parametrize("stage", ["event", "flood-extent", "composite", "merge"])
def test_stages_plan_with_config_budget(tmp_path, monkeypatch, stage):
    budgets = []

    def plan(budget_mb, bytes_per_pixel, height, width, stored=chunks.TILE):
        budgets.append(budget_mb)
        return ChunkPlan(16, 2)

    monkeypatch.setattr(chunks, "plan", plan)
    if stage == "merge":
        config, paths = ri.two_series(tmp_path, budget_mb=777)
        merge_stage.run(config, paths)
    else:
        config, paths = ri.single_series(tmp_path, budget_mb=777)
        modules = {"event": event, "flood-extent": flood_extent, "composite": composite}
        for name, module in modules.items():
            budgets.clear()
            module.run(config, paths)
            if name == stage:
                break
    assert budgets == [777]
