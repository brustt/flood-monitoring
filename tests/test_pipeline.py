from pathlib import Path
from types import SimpleNamespace

import pytest

from flood_monitoring import pipeline
from flood_monitoring.paths import EventPaths


def fake_stages(calls):
    def stage(name):
        def run(config, paths, force=False):
            calls.append((name, config.event, force))

        return SimpleNamespace(run=run)

    return {name: stage(name) for name in pipeline.STAGE_NAMES}


@pytest.fixture
def paths(tmp_path):
    return EventPaths(event="saintes-2026-02", data_root=tmp_path, repo_root=tmp_path / "repo")


def test_runs_stages_in_order_with_config_of_the_event(paths):
    calls = []
    pipeline.run_pipeline("saintes-2026-02", paths=paths, stages=fake_stages(calls))
    assert [c[0] for c in calls] == ["download", "stack", "despeckle", "event", "flood-extent"]
    assert {c[1] for c in calls} == {"saintes-2026-02"}


def test_download_skipped_when_subsets_exist(paths):
    tif = paths.raw / "sentinel-1-rtc" / "2026-02-05" / "item" / "vv.tif"
    tif.parent.mkdir(parents=True)
    tif.touch()
    calls = []
    pipeline.run_pipeline("saintes-2026-02", paths=paths, stages=fake_stages(calls))
    assert "download" not in [c[0] for c in calls]
    assert len(calls) == 4


def test_force_redoes_download_and_passes_force(paths):
    tif = paths.raw / "sentinel-1-rtc" / "2026-02-05" / "item" / "vv.tif"
    tif.parent.mkdir(parents=True)
    tif.touch()
    calls = []
    pipeline.run_pipeline("saintes-2026-02", force=True, paths=paths, stages=fake_stages(calls))
    assert calls[0] == ("download", "saintes-2026-02", True)
    assert all(c[2] for c in calls)


def test_unknown_event(paths):
    with pytest.raises(FileNotFoundError, match="no config for Event"):
        pipeline.run_pipeline("nowhere-2000-01", paths=paths, stages=fake_stages([]))


def test_creates_event_folders(paths):
    pipeline.run_pipeline("saintes-2026-02", paths=paths, stages=fake_stages([]))
    assert paths.processed.is_dir() and paths.interim.is_dir()
