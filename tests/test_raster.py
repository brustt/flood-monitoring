import json
import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin

from flood_monitoring import raster

CRS = "EPSG:32630"


def grid_da(data, dims=("y", "x")):
    h, w = data.shape[-2:]
    coords = {"y": 5000000 - 10 * (np.arange(h) + 0.5), "x": 500000 + 10 * (np.arange(w) + 0.5)}
    return xr.DataArray(data, dims=dims, coords=coords)


def test_partial_name():
    assert raster.partial(Path("a/flood-extent.tif")) == Path("a/flood-extent.tif.partial")


def test_write_partial_then_commit(tmp_path):
    path = tmp_path / "sub" / "m.tif"
    mask = grid_da(np.eye(5, dtype=bool)).chunk(2)
    (total,) = raster.write_partial([raster.Raster(mask, path, "uint8", CRS)], mask.sum())
    assert int(total) == 5
    assert raster.partial(path).exists() and not path.exists()
    raster.commit([path])
    assert path.exists() and not raster.partial(path).exists()
    with rasterio.open(path) as src:
        assert src.dtypes[0] == "uint8" and src.profile["tiled"] and src.block_shapes[0] == (256, 256)
        assert np.array_equal(src.read(1), np.eye(5, dtype="uint8"))


def test_write_partial_writes_exact_transform_and_crs(tmp_path):
    transform = from_origin(500000.123456789, 5000000.987654321, 10, 10)
    path = tmp_path / "t.tif"
    raster.write_partial([raster.Raster(grid_da(np.zeros((3, 4), bool)), path, "uint8", CRS, transform=transform)])
    with rasterio.open(raster.partial(path)) as src:
        assert src.transform == transform and src.crs.to_string() == CRS


def test_write_partial_ignores_inherited_attrs(tmp_path):
    mask = grid_da(np.ones((3, 3), bool))
    mask.attrs["nodata"] = -32768
    mask.encoding["_FillValue"] = -32768
    path = tmp_path / "m.tif"
    raster.write_partial([raster.Raster(mask, path, "uint8", CRS)])
    with rasterio.open(raster.partial(path)) as src:
        assert src.nodata is None and src.dtypes[0] == "uint8"
        assert set(src.tags()) == {"AREA_OR_POINT"}


def test_write_partial_accepts_numpy_backed_data(tmp_path):
    data = grid_da(np.arange(12, dtype="float32").reshape(3, 4))
    path = tmp_path / "f.tif"
    raster.write_partial([raster.Raster(data, path, "float32", CRS, nodata=-9999.0, long_name="x")])
    with rasterio.open(raster.partial(path)) as src:
        assert np.array_equal(src.read(1), data.values) and src.nodata == -9999.0


def test_failed_compute_leaves_no_final_file(tmp_path):
    def boom(block):
        raise RuntimeError("boom")

    data = grid_da(np.zeros((4, 4), "float32")).chunk(2)
    data = data.copy(data=data.data.map_blocks(boom, dtype="float32"))
    path = tmp_path / "f.tif"
    with pytest.raises(RuntimeError, match="boom"):
        raster.write_partial([raster.Raster(data, path, "float32", CRS)])
    assert not path.exists()


def test_write_json_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    raster.write_json(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}

    def failing(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError):
        raster.write_json(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 1}


def test_clear_removes_files_partials_and_folders(tmp_path):
    files = [tmp_path / "a.tif", raster.partial(tmp_path / "b.tif"), tmp_path / "work" / "x.tif"]
    for f in files:
        f.parent.mkdir(exist_ok=True)
        f.touch()
    raster.clear([tmp_path / "a.tif", tmp_path / "b.tif", tmp_path / "missing.tif"], [tmp_path / "work"])
    assert not any(f.exists() for f in files)


def test_sieve_keeps_region_cut_by_chunk_edges_and_drops_small_ones(tmp_path):
    mask = np.zeros((20, 20), bool)
    mask[3:9, 3:9] = True  # 36 px across the chunk edges at 4 and 8
    mask[15, 15] = True  # isolated
    lazy = grid_da(mask).chunk(4)
    (out,) = raster.sieve([lazy], CRS, tmp_path / "_sieve", size=20, connectedness=8)
    sieved = raster.read_mask(out)
    assert sieved[3:9, 3:9].all() and sieved.sum() == 36


def test_sieve_removes_raw_masks(tmp_path):
    work = tmp_path / "_sieve"
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "stale.tif").touch()
    files = raster.sieve([grid_da(np.ones((5, 5), bool)), grid_da(np.zeros((5, 5), bool))], CRS, work, 20, 8)
    assert not (work / "raw").exists()
    assert [f.parent for f in files] == [work / "sieved"] * 2
    assert raster.read_mask(files[0]).all() and not raster.read_mask(files[1]).any()


def test_open_mask_is_bool_on_like_coords(tmp_path):
    like = grid_da(np.zeros((6, 5), bool)).rio.write_crs(CRS)
    path = tmp_path / "m.tif"
    raster.write_partial([raster.Raster(grid_da(np.eye(6, 5, dtype=bool)), path, "uint8", CRS)])
    raster.commit([path])
    mask = raster.open_mask(path, like, side=4)
    assert mask.dtype == bool and mask.dims == ("y", "x") and mask.chunksizes["y"] == (4, 2)
    assert np.array_equal(mask["x"].values, like["x"].values) and mask.rio.crs == like.rio.crs
    assert np.array_equal(mask.values, np.eye(6, 5, dtype=bool))
    with pytest.raises(ValueError, match="shape"):
        raster.open_mask(path, grid_da(np.zeros((5, 5), bool)).rio.write_crs(CRS), side=4)
