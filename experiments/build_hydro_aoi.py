"""Build the Area of Interest for aquitaine_charente_2026-02 from the clipped BD TOPO major hydro network.

Merges the Nouvelle-Aquitaine and Pays de la Loire major files (output of clip_bdtopo_hydro_departments.py),
buffers the lines by BUFFER_M, dissolves, and writes one simplified WGS84 polygon small enough for a STAC `intersects`.

Usage: uv run --no-project --with geopandas python experiments/build_hydro_aoi.py
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd

HYDRO_DIR = Path("~/Documents/Projects/data/flood-monitoring/external/bdtopo_hydro").expanduser()
REGIONS = ("nouvelle_aquitaine", "pays_de_la_loire")
BUFFER_M = 5000
SIMPLIFY_M = 100  # Douglas-Peucker tolerance
METRIC_CRS = "EPSG:2154"
DST = HYDRO_DIR.parent / f"hydro_major_buffer{BUFFER_M}_simplified.gpkg"


def _n_vertices(geom) -> int:
    return sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in getattr(geom, "geoms", [geom]))


def main() -> None:
    lines = pd.concat(gpd.read_file(HYDRO_DIR / f"troncon_hydrographique_major_{r}_clipped.gpkg", columns=[])
                      for r in REGIONS)
    merged = lines.to_crs(METRIC_CRS).geometry.buffer(BUFFER_M).union_all()
    simplified = merged.buffer(SIMPLIFY_M).simplify(SIMPLIFY_M, preserve_topology=True)  # buffer first: still covers
    out = gpd.GeoDataFrame(geometry=[simplified], crs=METRIC_CRS).to_crs("EPSG:4326")
    out.to_file(DST, driver="GPKG", layer=DST.stem)
    print(f"{len(lines)} segments, area {simplified.area / 1e6:.0f} km2, "
          f"{_n_vertices(merged)} -> {_n_vertices(simplified)} vertices -> {DST}")


if __name__ == "__main__":
    main()
