import numpy as np
import rasterio
import xarray as xr

from flood_monitoring import export, raster

CRS = "EPSG:32630"


def grid(dtype):
    data = np.arange(64 * 64, dtype=dtype).reshape(64, 64)
    return xr.DataArray(data, dims=("y", "x"), coords={"y": 5e6 - 10 * np.arange(64.0), "x": 5e5 + 10 * np.arange(64.0)})


def test_processed_rasters_are_deflate_with_predictor(tmp_path):
    ints, floats = tmp_path / "i.tif", tmp_path / "f.tif"
    raster.write_partial([raster.Raster(grid("uint8"), ints, "uint8", CRS), raster.Raster(grid("float32"), floats, "float32", CRS)])
    raster.commit([ints, floats])
    for path, predictor in ((ints, "2"), (floats, "3")):
        with rasterio.open(path) as ds:
            assert ds.compression.value == "DEFLATE"
            assert ds.tags(ns="IMAGE_STRUCTURE")["PREDICTOR"] == predictor


def test_cog_output_is_deflate(tmp_path):
    src, dst = tmp_path / "s.tif", tmp_path / "d.tif"
    raster.write_partial([raster.Raster(grid("float32"), src, "float32", CRS)])
    raster.commit([src])
    export.write_cog(src, dst, categorical=False)
    with rasterio.open(dst) as ds:
        assert ds.compression.value == "DEFLATE"
        assert ds.tags(ns="IMAGE_STRUCTURE")["PREDICTOR"] == "3"
