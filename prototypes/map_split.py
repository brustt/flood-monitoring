"""Split-screen Before/After Composites in folium (issue #9). Throwaway.

Builds three maps on the real saintes-2026-02 Composites, written to prototypes/map_split/:

  a_overlay.html   ImageOverlays, each in its own pane, under SideBySideLayers (+ a 3-line JS patch)
  b_tiles.html     the Composites cut to an XYZ tile folder with gdal2tiles, TileLayers under SideBySideLayers
  c_dualmap.html   folium.plugins.DualMap, two synchronised maps

Flood Extent is an RGBA ImageOverlay in the After pane (so it only shows right of the divider in a), toggled from the layer control.

    uv run prototypes/map_split.py [a|b|c ...]
"""
import subprocess
import sys
from pathlib import Path

import folium
import numpy as np
import rasterio
from folium.plugins import DualMap, SideBySideLayers
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject

from flood_monitoring.composite import render_rgb
from flood_monitoring.config import load_event_config
from flood_monitoring.paths import event_paths

EVENT = "saintes-2026-02"
OUT = Path(__file__).parent / "map_split"
paths = event_paths(EVENT)
config = load_event_config(EVENT)
ranges = {
    "vv": config.composite.vv,
    "vh": config.composite.vh,
    "vv_minus_vh": config.composite.vv_minus_vh,
}
W, S, E, N = config.area_of_interest
CENTER = [(S + N) / 2, (W + E) / 2]
OUT.mkdir(exist_ok=True)


def to_wgs84(array: np.ndarray, src: rasterio.DatasetReader, nodata: float):
    """Reproject (bands, y, x) to EPSG:4326; returns (array, ((south, west), (north, east)))."""
    t, w, h = calculate_default_transform(src.crs, "EPSG:4326", src.width, src.height, *src.bounds)
    out = np.full((array.shape[0], h, w), nodata, dtype=array.dtype)
    reproject(array, out, src_transform=src.transform, src_crs=src.crs, dst_transform=t,
              dst_crs="EPSG:4326", resampling=Resampling.nearest, dst_nodata=nodata or None)
    west, north = t * (0, 0)
    east, south = t * (w, h)
    return out, ((south, west), (north, east))


def rgb_png(path: Path, name: str) -> tuple[Path, tuple]:
    with rasterio.open(path) as src:
        comp, bounds = to_wgs84(src.read(), src, -9999.0)
    rgb = render_rgb(comp, ranges)
    png = OUT / f"{name}.png"
    alpha = np.where((comp == -9999.0).all(axis=0), 0, 255).astype("uint8")
    Image.fromarray(np.dstack([rgb, alpha])).save(png)
    return png, bounds


def extent_png() -> tuple[Path, tuple]:
    with rasterio.open(paths.flood_extent) as src:
        data, bounds = to_wgs84(src.read(1)[None], src, 0)
    wet = data[0] > 0
    rgba = np.zeros((*wet.shape, 4), "uint8")
    rgba[wet] = (0, 220, 255, 220)
    png = OUT / "flood-extent.png"
    Image.fromarray(rgba).save(png)
    return png, bounds


def add_extent(target, name="Flood Extent", pane=None):
    png, bounds = extent_png()
    folium.raster_layers.ImageOverlay(str(png), bounds=list(bounds), name=name, pane=pane,
                                      show=True, zindex=500).add_to(target)


# Panes: Leaflet's overlayPane is 400; extent must sit above the clipped panes.
PANES = """
{% macro script(this, kwargs) %}
  var m = {{ this._parent.get_name() }};
  ['before','after'].forEach(function(n){ m.createPane(n).style.zIndex = 300; });
{% endmacro %}
"""
# SideBySide calls layer.getContainer(); ImageOverlay only has getElement(). Return the pane
# instead: it is not transformed, so the plugin's clip rect (layer-point space) lines up.
PATCH = "L.ImageOverlay.prototype.getContainer = function(){ return this.getPane(); };"


def map_a():
    from branca.element import MacroElement, Element
    from jinja2 import Template
    m = folium.Map(CENTER, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
    m.get_root().script.add_child(Element(PATCH))
    pane_js = MacroElement(); pane_js._template = Template(PANES); pane_js.add_to(m)
    layers = {}
    for key, path in (("before", paths.before_composite), ("after", paths.after_composite)):
        png, bounds = rgb_png(path, key)
        layers[key] = folium.raster_layers.ImageOverlay(
            str(png), bounds=list(bounds), name=key.capitalize(), pane=key).add_to(m)
    add_extent(m, pane="after")  # shares the After pane, so the split clips it too
    SideBySideLayers(layers["before"], layers["after"]).add_to(m)
    folium.LayerControl().add_to(m)
    m.save(OUT / "a_overlay.html")


def map_b():
    """Composites -> 3-band byte GeoTIFF in EPSG:3857 -> gdal2tiles XYZ folder."""
    from rasterio.enums import Resampling as R
    m = folium.Map(CENTER, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
    tiles = {}
    for key, path in (("before", paths.before_composite), ("after", paths.after_composite)):
        png, _ = rgb_png(path, key)
        rgba = np.asarray(Image.open(png))
        with rasterio.open(path) as src:
            comp = src.read()
            prof = src.profile | dict(count=4, dtype="uint8", nodata=None, compress="deflate")
        rgb = render_rgb(comp, ranges)
        a = np.where((comp == -9999.0).all(axis=0), 0, 255).astype("uint8")
        tif = OUT / f"{key}-rgba.tif"
        with rasterio.open(tif, "w", **prof) as dst:
            dst.write(np.dstack([rgb, a]).transpose(2, 0, 1))
        folder = OUT / "tiles" / key
        subprocess.run(["gdal2tiles.py", "--xyz", "-z", "9-15", "-w", "none", "--processes=4",
                        str(tif), str(folder)], check=True, capture_output=True)
        tiles[key] = folium.TileLayer(f"tiles/{key}/{{z}}/{{x}}/{{y}}.png", attr=key, name=key.capitalize(),
                                      min_zoom=9, max_zoom=15, overlay=True).add_to(m)
    SideBySideLayers(tiles["before"], tiles["after"]).add_to(m)
    add_extent_b = extent_png()
    folium.raster_layers.ImageOverlay(str(add_extent_b[0]), bounds=list(add_extent_b[1]),
                                      name="Flood Extent", zindex=500).add_to(m)
    folium.LayerControl().add_to(m)
    m.save(OUT / "b_tiles.html")


def map_c():
    dm = DualMap(location=CENTER, zoom_start=12, tiles="OpenStreetMap", layout="horizontal")
    for sub, key, path in ((dm.m1, "Before", paths.before_composite), (dm.m2, "After", paths.after_composite)):
        png, bounds = rgb_png(path, key.lower())
        folium.raster_layers.ImageOverlay(str(png), bounds=list(bounds), name=key).add_to(sub)
        add_extent(sub)
        folium.LayerControl().add_to(sub)
    dm.save(OUT / "c_dualmap.html")


if __name__ == "__main__":
    which = sys.argv[1:] or ["a", "b", "c"]
    for k in which:
        {"a": map_a, "b": map_b, "c": map_c}[k]()
        print("built", k)
