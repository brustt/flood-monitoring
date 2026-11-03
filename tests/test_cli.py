from types import SimpleNamespace

import pytest

from flood_monitoring import cli
from flood_monitoring.config import load_event_config
from flood_monitoring.paths import EventPaths

ORDER = ["download", "stack", "despeckle", "event", "flood-extent", "composite", "merge", "export"]


def fake_stages(calls):
    def stage(name):
        def run(config, paths, force=False):
            calls.append((name, config.event, force))

        return SimpleNamespace(run=run)

    return {name: stage(name) for name in cli.STAGES}


@pytest.fixture(autouse=True)
def no_reexec(monkeypatch):
    """main() would replace the pytest process with a re-executed CLI."""
    monkeypatch.setattr(cli, "ensure_clean_environment", lambda argv: None)


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


def test_stage_order_has_merge_between_composite_and_export():
    assert list(cli.STAGES) == ORDER


def test_runs_all_stages_in_order(paths, config):
    calls = []
    cli.run_stages(config, paths, ORDER, stages=fake_stages(calls))
    assert [c[0] for c in calls] == ORDER
    assert {c[1] for c in calls} == {"saintes-2026-02"}


def test_download_runs_when_some_subsets_exist(paths, config):
    """A partial download must be resumed: eo-stac skips the files already on disk."""
    add_subset(paths)
    calls = []
    cli.run_stages(config, paths, ORDER, stages=fake_stages(calls))
    assert [c[0] for c in calls] == ORDER


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


def test_main_defaults_to_saintes_and_reports_missing_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FLOOD_MONITORING_DATA_ROOT", str(tmp_path))
    assert cli.main(["run", "--stage", "export"]) == 1
    assert "run the flood-extent stage first" in capsys.readouterr().err


def test_per_series_stages_run_for_every_orbit_and_merge_once(paths, config):
    from dataclasses import replace

    calls = []

    def stage(name):
        def run(cfg, p, force=False):
            calls.append((name, cfg.series.relative_orbits, p.orbit, force))

        return SimpleNamespace(run=run)

    stages = {name: stage(name) for name in cli.STAGES}
    multi = replace(config, series=replace(config.series, relative_orbits=(8, 110)))
    cli.run_stages(multi, paths, ["composite", "merge", "export"], force=True, stages=stages)
    assert calls == [
        ("composite", (8,), 8, True),
        ("composite", (110,), 110, True),
        ("merge", (8, 110), None, True),
        ("export", (8, 110), None, True),
    ]
    assert paths.series_paths(110).processed.is_dir()


PER_SERIES = ORDER[:6]


def multi_orbit(config):
    from dataclasses import replace

    return replace(config, series=replace(config.series, relative_orbits=(8, 110)))


def recording_stages(calls):
    return {
        name: SimpleNamespace(run=lambda config, paths, force=False, name=name: calls.append((name, paths.orbit)))
        for name in cli.STAGES
    }


def producing_stages(calls):
    """Fake stages that write their output file, as the real ones do."""
    outputs = {
        "download": lambda p: [p.raw / "subset.tif"],
        "stack": lambda p: [p.cube],
        "despeckle": lambda p: [p.filtered_cube],
        "composite": lambda p: [p.before_composite, p.after_composite],
    }

    def stage(name):
        def run(config, paths, force=False):
            calls.append((name, paths.orbit))
            for out in outputs.get(name, lambda p: [])(paths):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.touch()

        return SimpleNamespace(run=run)

    return {name: stage(name) for name in cli.STAGES}


def test_per_series_stages_run_orbit_by_orbit(paths, config):
    calls = []
    cli.run_stages(multi_orbit(config), paths, PER_SERIES, stages=recording_stages(calls))
    assert calls == [(name, orbit) for orbit in (8, 110) for name in PER_SERIES]


def test_intermediates_are_deleted_once_the_next_stage_produced_its_result(paths, config):
    cli.run_stages(config, paths, PER_SERIES, stages=producing_stages([]))
    assert not paths.raw.exists() and not paths.cube.exists() and not paths.filtered_cube.exists()
    assert paths.before_composite.exists() and paths.after_composite.exists()


def test_keep_intermediates_keeps_them_all(paths, config):
    cli.run_stages(config, paths, PER_SERIES, stages=producing_stages([]), keep_intermediates=True)
    assert paths.raw.exists() and paths.cube.exists() and paths.filtered_cube.exists()


def test_only_one_orbits_intermediates_exist_at_a_time(paths, config):
    seen = []
    stages = producing_stages([])
    stack = stages["stack"].run

    def stack_then_look(cfg, p, force=False):
        stack(cfg, p, force)
        seen.append(sorted(q.parent.name for q in paths.interim.glob("orbit-*/cube.nc")))

    stages["stack"] = SimpleNamespace(run=stack_then_look)
    cli.run_stages(multi_orbit(config), paths, PER_SERIES, stages=stages)
    assert seen == [["orbit-8"], ["orbit-110"]]


def test_a_stage_that_produced_nothing_keeps_its_input(paths, config):
    stages = producing_stages([])
    stages["composite"] = SimpleNamespace(run=lambda config, paths, force=False: None)  # e.g. no VH: no Composites
    cli.run_stages(config, paths, PER_SERIES, stages=stages)
    assert paths.filtered_cube.exists()


def test_stages_up_to_despeckle_are_skipped_when_a_later_requested_stage_is_done(paths, config):
    paths.ensure()
    paths.before_composite.touch()
    calls = []
    cli.run_stages(config, paths, PER_SERIES, stages=recording_stages(calls))
    assert [c[0] for c in calls] == ["event", "flood-extent", "composite"]


def test_force_does_not_skip_superseded_stages(paths, config):
    paths.ensure()
    paths.before_composite.touch()
    calls = []
    cli.run_stages(config, paths, PER_SERIES, force=True, stages=recording_stages(calls))
    assert [c[0] for c in calls] == PER_SERIES


def test_a_single_requested_stage_is_never_skipped(paths, config):
    paths.ensure()
    paths.before_composite.touch()
    calls = []
    cli.run_stages(config, paths, ["despeckle"], stages=recording_stages(calls))
    assert [c[0] for c in calls] == ["despeckle"]
