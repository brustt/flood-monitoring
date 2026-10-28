"""Area of Interest as a pixel mask."""

from __future__ import annotations

import numpy as np
import rasterio.features
import xarray as xr
from pyproj import Transformer
from rasterio.transform import Affine
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform


def aoi_mask(polygon: BaseGeometry, crs, transform: Affine, shape_yx: tuple[int, int]) -> np.ndarray:
    """Boolean (y, x) mask of the pixels whose centre lies inside *polygon* (EPSG:4326) on the given grid."""
    project = Transformer.from_crs(4326, crs, always_xy=True).transform
    projected = shapely_transform(project, polygon)
    return rasterio.features.geometry_mask(
        [projected], out_shape=shape_yx, transform=transform, invert=True, all_touched=False
    )


def written_transform(da: xr.DataArray) -> Affine:
    """The transform written on *da* with ``rio.write_transform`` if any, else derived from its coords.

    rioxarray's ``rio.transform()`` always derives it from the coords when they exist, which can differ
    from the written one in the last bits.
    """
    grid_mapping = da.coords.get(da.rio.grid_mapping)
    if grid_mapping is not None and "GeoTransform" in grid_mapping.attrs:
        return Affine.from_gdal(*(float(v) for v in grid_mapping.attrs["GeoTransform"].split()))
    return da.rio.transform()


def mask_like(polygon: BaseGeometry, template: xr.DataArray) -> xr.DataArray:
    """Lazy boolean (y, x) :func:`aoi_mask` on the grid and y/x chunks of a georeferenced, dask-backed *template*.

    The mask is computed once on the whole grid (1 B/px, reserved from the memory budget), so it is the
    same whatever the chunking.
    """
    transform = written_transform(template)
    mask = aoi_mask(polygon, template.rio.crs, transform, template.shape[-2:])
    da = xr.DataArray(mask, dims=("y", "x"), coords={"y": template["y"].values, "x": template["x"].values})
    da = da.rio.write_crs(template.rio.crs).rio.write_transform(transform)
    return da.chunk({"y": template.chunksizes["y"], "x": template.chunksizes["x"]})


def template_mask(polygon: BaseGeometry, template: xr.DataArray) -> np.ndarray:
    """:func:`aoi_mask` on the grid of a georeferenced (y, x) *template*."""
    return aoi_mask(polygon, template.rio.crs, template.rio.transform(), template.shape[-2:])
