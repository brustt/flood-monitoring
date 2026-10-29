from datetime import date

from flood_monitoring import stack
from flood_monitoring.config import Series

SERIES = Series("sentinel-1-rtc", date(2026, 1, 1), date(2026, 3, 1), "descending", "IW", (8,), ("vv",))


def touch(raw, day, asset):
    tif = raw / "sentinel-1-rtc" / day / "item" / f"item_{asset}_sub-abc.tif"
    tif.parent.mkdir(parents=True, exist_ok=True)
    tif.touch()
    return tif


def test_subsets_keep_only_the_series_dates_and_assets(tmp_path):
    kept = [touch(tmp_path, "2026-01-01", "vv"), touch(tmp_path, "2026-02-10", "vv"), touch(tmp_path, "2026-03-01", "vv")]
    touch(tmp_path, "2025-12-31", "vv")
    touch(tmp_path, "2026-03-02", "vv")
    touch(tmp_path, "2026-02-10", "vh")

    assert stack.subsets(tmp_path, SERIES) == sorted(kept)
