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


def template_mask(polygon: BaseGeometry, template: xr.DataArray) -> np.ndarray:
    """:func:`aoi_mask` on the grid of a georeferenced (y, x) *template*."""
    return aoi_mask(polygon, template.rio.crs, template.rio.transform(), template.shape[-2:])
