from types import SimpleNamespace

import pytest

from flood_monitoring import cli
from flood_monitoring.config import load_event_config
from flood_monitoring.paths import EventPaths

ORDER = ["download", "stack", "despeckle", "event", "flood-extent", "composite", "export"]


def fake_stages(calls):
    def stage(name):
        def run(config, paths, force=False):
            calls.append((name, config.event, force))

        return SimpleNamespace(run=run)

    return {name: stage(name) for name in cli.STAGES}


@pytest.fixture
def paths(tmp_path):
    return EventPaths(event="saintes-2026-02", data_root=tmp_path, repo_root=tmp_path / "repo")


@pytest.fixture
def config():
    return load_event_config("saintes-2026-02")


def add_subset(paths):
    tif = paths.raw / "sentinel-1-rtc" / "2026-02-05" / "item" / "vv.tif"
    tif.parent.mkdir(parents=True)
    tif.touch()


def test_stage_order_includes_export():
    assert list(cli.STAGES) == ORDER


def test_runs_all_stages_in_order(paths, config):
    calls = []
    cli.run_stages(config, paths, ORDER, stages=fake_stages(calls))
    assert [c[0] for c in calls] == ORDER
    assert {c[1] for c in calls} == {"saintes-2026-02"}


def test_download_skipped_when_subsets_exist(paths, config):
    add_subset(paths)
    calls = []
    cli.run_stages(config, paths, ORDER, stages=fake_stages(calls))
    assert [c[0] for c in calls] == ORDER[1:]


def test_force_redoes_download_and_passes_force(paths, config):
    add_subset(paths)
    calls = []
    cli.run_stages(config, paths, ORDER, force=True, stages=fake_stages(calls))
    assert calls[0] == ("download", "saintes-2026-02", True)
    assert all(c[2] for c in calls)


def test_creates_event_folders(paths, config):
    cli.run_stages(config, paths, [], stages=fake_stages([]))
    assert paths.processed.is_dir() and paths.interim.is_dir()


def test_main_unknown_event_returns_1(capsys):
    assert cli.main(["run", "nowhere-2000-01"]) == 1
    assert "no config for Event" in capsys.readouterr().err


def test_main_defaults_to_saintes_and_reports_unbuilt_stage(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FLOOD_MONITORING_DATA_ROOT", str(tmp_path))
    assert cli.main(["run", "--stage", "export"]) == 1
    assert "export stage is not built yet" in capsys.readouterr().err
