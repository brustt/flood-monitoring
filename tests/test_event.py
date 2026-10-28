import json

import pytest

import reference_inputs as ri
from flood_monitoring import detection, event


def test_too_short_series_error_has_orbit_prefix(tmp_path):
    config, paths = ri.single_series(tmp_path, dates=8)
    with pytest.raises(ValueError, match="^relative orbit 8: need more than 8 Acquisitions"):
        event.run(config, _orbit(paths, 8))


def _orbit(paths, orbit):
    """The paths of relative orbit *orbit*, sharing the single-series Filtered Cube."""
    series = paths.series_paths(orbit)
    series.interim.mkdir(parents=True)
    paths.filtered_cube.rename(series.filtered_cube)
    return series


def test_killed_forced_run_removes_summary(tmp_path, monkeypatch):
    config, paths = ri.single_series(tmp_path)
    paths.processed.mkdir(parents=True)
    paths.summary.write_text(json.dumps({"event_start": "2026-01-02"}))

    def killed(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(detection, "series_drop_counts", killed)
    with pytest.raises(KeyboardInterrupt):
        event.run(config, paths, force=True)
    assert not paths.summary.exists()
