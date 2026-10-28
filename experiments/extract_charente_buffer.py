"""Extract the Charente river (BD TOPAGE) within 200 km of Saintes, buffered by 200 m.

Usage: python experiments/extract_charente_buffer.py
"""
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

SRC = Path("~/Documents/Projects/data/BD_TOPAGE/CoursEau_Topage2026.gpkg").expanduser()
DST = Path("~/Documents/Projects/data/flood-monitoring/external/charente_buffer200.gpkg").expanduser()

SAINTES = Point(-0.6333, 45.7458)  # lon, lat (WGS84)
RADIUS_M = 200_000
BUFFER_M = 200
RIVER_NAME = "la Charente"  # exact TopoOH value; excludes "Bras de la Charente", canals, etc.
METRIC_CRS = "EPSG:2154"  # Lambert-93


def main() -> None:
    rivers = gpd.read_file(SRC, where=f"TopoOH = '{RIVER_NAME}'").to_crs(METRIC_CRS)
    center = gpd.GeoSeries([SAINTES], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    zone = center.buffer(RADIUS_M)

    clipped = rivers.assign(geometry=rivers.intersection(zone))
    clipped = clipped[~clipped.is_empty]
    clipped = clipped.assign(geometry=clipped.buffer(BUFFER_M))

    DST.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(DST, driver="GPKG", layer="charente_buffer200")
    print(f"{len(clipped)} features -> {DST}")


if __name__ == "__main__":
    main()
