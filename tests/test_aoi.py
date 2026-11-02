import dask.array
import numpy as np
import pytest
import xarray as xr
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform

from flood_monitoring.aoi import aoi_mask, mask_like, written_transform

CRS = "EPSG:32630"
SIZE = 30
TRANSFORM = from_origin(500000, 5000000, 10, 10)


def lonlat(*corners):
    return shapely_transform(Transformer.from_crs(CRS, 4326, always_xy=True).transform, Polygon(corners))


def template(side):
    xs = 500000 + 10 * (np.arange(SIZE) + 0.5)
    ys = 5000000 - 10 * (np.arange(SIZE) + 0.5)
    data = dask.array.zeros((2, SIZE, SIZE), chunks=(2, side, side))
    return xr.DataArray(data, dims=("time", "y", "x"), coords={"y": ys, "x": xs}).rio.write_crs(CRS)


POLYGONS = [
    lonlat((500013, 4999987), (500250, 4999960), (500280, 4999720), (500020, 4999760)),
    # east edge exactly on the centres of column 10
    lonlat((500000, 5000000), (500105, 5000000), (500105, 4999700), (500000, 4999700)),
]


@pytest.mark.parametrize("side", [3, 7, SIZE])
@pytest.mark.parametrize("polygon", POLYGONS)
def test_mask_like_equals_aoi_mask_for_any_chunking(side, polygon):
    lazy = mask_like(polygon, template(side))
    assert lazy.dims == ("y", "x") and lazy.dtype == bool
    assert lazy.chunksizes["y"] == template(side).chunksizes["y"]
    assert lazy.rio.crs == template(side).rio.crs
    expected = aoi_mask(polygon, CRS, TRANSFORM, (SIZE, SIZE))
    assert expected.any() and not expected.all()
    assert np.array_equal(lazy.values, expected)


def test_mask_like_uses_a_written_transform_exactly():
    exact = from_origin(500000.000000001, 5000000.000000001, 10, 10)
    lazy = mask_like(POLYGONS[1], template(7).rio.write_transform(exact))
    assert written_transform(lazy) == exact
    assert np.array_equal(lazy.values, aoi_mask(POLYGONS[1], CRS, exact, (SIZE, SIZE)))


def test_chunk_window_marks_the_chunks_the_polygon_touches_with_a_one_pixel_margin():
    from flood_monitoring.aoi import chunk_window

    # a 30 x 30 px grid in 10 x 10 chunks; the polygon sits inside chunk (0, 0), 5 px from its east edge
    inside = lonlat((500010, 4999990), (500040, 4999990), (500040, 4999960), (500010, 4999960))
    window = chunk_window(inside, CRS, TRANSFORM, (SIZE, SIZE), 10)
    assert window.shape == (3, 3)
    assert window.tolist() == [[True, False, False]] + [[False] * 3] * 2
    # one pixel from the east edge of chunk (0, 0): the margin reaches into chunk (0, 1)
    edge = lonlat((500010, 4999990), (500100, 4999990), (500100, 4999960), (500010, 4999960))
    assert chunk_window(edge, CRS, TRANSFORM, (SIZE, SIZE), 10)[0].tolist() == [True, True, False]


def test_chunk_window_pads_a_grid_that_is_not_a_multiple_of_the_chunk_side():
    from flood_monitoring.aoi import chunk_window

    everything = lonlat((499000, 5001000), (501000, 5001000), (501000, 4999000), (499000, 4999000))
    assert chunk_window(everything, CRS, TRANSFORM, (SIZE, SIZE), 7).shape == (5, 5)
    assert chunk_window(everything, CRS, TRANSFORM, (SIZE, SIZE), 7).all()
