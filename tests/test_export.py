import json
from datetime import datetime

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from flood_monitoring import export
from flood_monitoring.config import load_event_config
from flood_monitoring.paths import EventPaths

SUMMARY = {"event_start": "2026-02-05", "before": "2026-01-30", "peak": "2026-02-23"}
SIZE = 600


@pytest.fixture
def config():
    return load_event_config("saintes-2026-02")


def write_raster(path, data, dtype, nodata):
    bands = data if data.ndim == 3 else data[None]
    with rasterio.open(
        path, "w", driver="GTiff", width=SIZE, height=SIZE, count=len(bands), dtype=dtype,
        crs="EPSG:32630", transform=from_origin(500000, 5100000, 10, 10), nodata=nodata,
    ) as dst:
        dst.write(bands.astype(dtype))


def mask(seed=0):
    return np.random.default_rng(seed).random((SIZE, SIZE)) > 0.5


def composite(seed=0):
    return np.random.default_rng(seed).random((3, SIZE, SIZE)) * 10 - 20


@pytest.fixture
def paths(tmp_path):
    p = EventPaths(event="saintes-2026-02", data_root=tmp_path / "data", repo_root=tmp_path / "repo")
    p.ensure()
    write_raster(p.flood_extent, mask(), "uint8", None)
    write_raster(p.before_composite, composite(1), "float32", -9999.0)
    write_raster(p.after_composite, composite(2), "float32", -9999.0)
    p.summary.write_text(json.dumps(SUMMARY))
    return p


def write_project(paths, text=None):
    (paths.publish / "project.yaml").write_text(
        text
        or "slug: flood-monitoring\ntitle: Flood monitoring\ndescription: Flood extent from Sentinel-1.\n"
        "licence: CC-BY-4.0\nattribution: brustt\n"
    )


def test_manifest_dates_and_window_come_from_config_and_summary(config):
    m = export.event_manifest(config, SUMMARY)
    assert m["event"] == "saintes-2026-02"
    assert m["title"] == "Saintes flood, February 2026"
    assert (m["start"], m["end"]) == ("2026-02-01", "2026-03-01")
    assert m["compare"] == ["s1-before", "s1-after"]
    layers = {l["slug"]: l for l in m["layers"]}
    assert list(layers) == ["flood-extent", "s1-before", "s1-after"]
    assert layers["s1-before"]["acquired"] == "2026-01-30"
    assert layers["s1-after"]["acquired"] == "2026-02-23"


def test_manifest_layer_roles_hints_and_attribution(config):
    layers = {l["slug"]: l for l in export.event_manifest(config, SUMMARY)["layers"]}
    extent = layers["flood-extent"]
    assert extent["role"] == "result" and extent["file"] == "../outputs/saintes-2026-02/flood-extent.tif"
    assert [c["value"] for c in extent["categories"]] == [0, 1]
    assert extent["categories"][0]["transparent"] is True
    assert extent["categories"][1] == {"value": 1, "color": "#1f78b4", "label": "Flooded"}
    for slug in ("s1-before", "s1-after"):
        layer = layers[slug]
        assert layer["role"] == "imagery"
        assert "Copernicus" in layer["attribution"]
        assert layer["rgb"] == {"bands": [1, 2, 3], "ranges": [[-20.0, -3.0], [-26.0, -9.0], [2.0, 12.0]]}
    assert "attribution" not in extent


def test_flood_extent_cog_is_uint8_nodata_255_with_nearest_overviews(tmp_path):
    src, dst = tmp_path / "src.tif", tmp_path / "out.tif"
    write_raster(src, mask(), "uint8", None)
    export.write_cog(src, dst, categorical=True, nodata=255)
    with rasterio.open(dst) as r:
        assert r.dtypes[0] == "uint8" and r.nodata == 255
        assert r.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert r.overviews(1)
        assert set(np.unique(r.read(1, out_shape=(75, 75)))) <= {0, 1}
        assert r.crs.to_epsg() == 32630


def test_composite_cog_keeps_three_bands_and_nodata(tmp_path):
    src, dst = tmp_path / "src.tif", tmp_path / "out.tif"
    write_raster(src, composite(), "float32", -9999.0)
    export.write_cog(src, dst, categorical=False)
    with rasterio.open(dst) as r:
        assert r.count == 3 and r.nodata == -9999.0 and r.overviews(1)


def test_run_exports_validates_and_archives(config, paths):
    write_project(paths)
    export.run(config, paths, executed_at=datetime(2026, 10, 13, 9, 30, 0))
    archive = paths.data_root / "outputs" / "20261013T093000" / "saintes-2026-02"
    for name in ("flood-extent", "s1-before", "s1-after"):
        assert (paths.outputs / f"{name}.tif").exists()
        assert (archive / f"{name}.tif").exists()
    assert (paths.publish / "saintes-2026-02.publish.yaml").exists()


MULTI_SUMMARY = {
    **SUMMARY,
    "series": [
        {"relative_orbit": 8, "rank": 1, "before": "2026-01-30", "peak": "2026-02-23"},
        {"relative_orbit": 110, "rank": 2, "before": "2026-01-31", "peak": "2026-02-24"},
    ],
}


def test_multi_orbit_manifest_has_a_hidden_orbit_map_labelled_with_dates(config):
    layers = {l["slug"]: l for l in export.event_manifest(config, MULTI_SUMMARY)["layers"]}
    orbit_map = layers["orbit-map"]
    assert orbit_map["shown"] is False
    assert [c["label"] for c in orbit_map["categories"]] == [
        "orbit 8: before 2026-01-30, after 2026-02-23",
        "orbit 110: before 2026-01-31, after 2026-02-24",
    ]
    assert "orbit-map" not in {l["slug"] for l in export.event_manifest(config, SUMMARY)["layers"]}


def test_run_exports_and_validates_the_orbit_map(config, paths):
    write_project(paths)
    paths.summary.write_text(json.dumps(MULTI_SUMMARY))
    write_raster(paths.orbit_map, mask(3).astype("uint8") + 1, "uint8", 0)
    export.run(config, paths, executed_at=datetime(2026, 10, 13, 9, 30, 0))
    assert (paths.outputs / "orbit-map.tif").exists()


def test_run_fails_when_validation_fails(config, paths):
    write_project(paths, "slug: Bad Slug\n")
    with pytest.raises(RuntimeError, match="validation"):
        export.run(config, paths)


def test_run_asks_for_missing_inputs(config, paths):
    write_project(paths)
    paths.after_composite.unlink()
    with pytest.raises(FileNotFoundError, match="composite"):
        export.run(config, paths)
