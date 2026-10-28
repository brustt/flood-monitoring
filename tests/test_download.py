from datetime import date

import geopandas as gpd
import pytest

from flood_monitoring import download
from reference_inputs import config, grid_polygon, paths


@pytest.fixture
def searched(monkeypatch):
    """Keyword arguments of the search_items call; the search finds nothing."""
    calls = []

    def search_items(source, **kwargs):
        calls.append(kwargs)
        return gpd.GeoDataFrame()

    monkeypatch.setattr(download, "search_items", search_items)
    monkeypatch.setattr(download.STACSource, "from_url", lambda url: None)
    return calls


def run(cfg, tmp_path):
    with pytest.raises(RuntimeError, match="no sentinel-1-rtc items found"):
        download.run(cfg, paths(tmp_path))


def test_search_uses_the_aoi_polygon(searched, tmp_path):
    polygon = grid_polygon(48)
    run(config(dates=[date(2026, 2, 1)], aoi_polygon=polygon), tmp_path)
    assert searched[0]["polygon"] is polygon


def test_search_of_a_rectangle_uses_the_bounding_box(searched, tmp_path):
    cfg = config(dates=[date(2026, 2, 1)])
    run(cfg, tmp_path)
    assert searched[0]["polygon"] is None
    assert searched[0]["bbox"] == list(cfg.area_of_interest)
