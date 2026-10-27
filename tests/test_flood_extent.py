import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from flood_monitoring import flood_extent
from flood_monitoring.config import load_event_config
from flood_monitoring.flood_extent import read_mask
from flood_monitoring.paths import EventPaths

SIZE = 40
DATES = pd.date_range("2026-01-01", periods=6, freq="6D")  # Event Start is the 4th
WATER, LAND = 0.01, 0.1  # linear power: -20 dB, -10 dB
PIXEL_KM2 = 10 * 10 / 1e6


def filtered_cube() -> xr.Dataset:
    """River (rows 0-4) in every date, a flood block growing after the Event Start, and one
    isolated water pixel on the Peak that the sieve must remove."""
    vv = np.full((len(DATES), SIZE, SIZE), LAND, dtype="float32")
    vv[:, :5] = WATER
    vv[3, 10:15, :10] = WATER  # 50 px
    vv[4, 10:20, :10] = WATER  # 100 px: the Peak
    vv[4, 30, 30] = WATER  # speckle
    vv[5, 10:12, :10] = WATER  # 20 px
    ds = xr.Dataset(
        {"VV": (("time", "y", "x"), vv)},
        coords={
            "time": DATES,
            "y": 5_100_000 - 10 * np.arange(SIZE) - 5.0,
            "x": 500_000 + 10 * np.arange(SIZE) + 5.0,
        },
    )
    return ds.rio.write_crs("EPSG:32630")


@pytest.fixture
def paths(tmp_path):
    p = EventPaths(event="saintes-2026-02", data_root=tmp_path / "data", repo_root=tmp_path / "repo")
    p.ensure()
    filtered_cube().to_netcdf(p.filtered_cube)
    p.summary.write_text(json.dumps({"event_start": "2026-01-19"}))
    return p


def test_run_writes_masks_extent_and_areas(paths):
    flood_extent.run(load_event_config("saintes-2026-02"), paths)

    post = ["2026-01-19", "2026-01-25", "2026-01-31"]
    assert sorted(p.stem for p in paths.water_masks.glob("*.tif")) == post
    permanent = read_mask(paths.permanent_water)
    assert permanent.sum() == 5 * SIZE and permanent[:5].all()
    extent = read_mask(paths.flood_extent)
    assert extent.sum() == 100 and extent[10:20, :10].all()
    assert not read_mask(paths.water_mask("2026-01-25"))[30, 30]
    assert not paths.sieve_work.exists()

    summary = json.loads(paths.summary.read_text())
    assert summary["event_start"] == "2026-01-19"
    assert summary["peak"] == "2026-01-25"
    assert summary["flooded_area_km2"] == {
        "2026-01-19": round(50 * PIXEL_KM2, 3),
        "2026-01-25": round(100 * PIXEL_KM2, 3),
        "2026-01-31": round(20 * PIXEL_KM2, 3),
    }
    assert summary["permanent_water_km2"] == round(5 * SIZE * PIXEL_KM2, 3)
    assert summary["flood_extent_km2"] == round(100 * PIXEL_KM2, 3)
    assert summary["rules_extent"] == {
        "water_db": -15.0, "permanent_fraction": 0.5, "sieve_size": 20, "sieve_connectedness": 8,
    }


def test_run_skips_when_flood_extent_exists(paths, capsys):
    paths.flood_extent.write_bytes(b"")
    flood_extent.run(load_event_config("saintes-2026-02"), paths)
    assert "skip" in capsys.readouterr().out
    assert "peak" not in json.loads(paths.summary.read_text())
