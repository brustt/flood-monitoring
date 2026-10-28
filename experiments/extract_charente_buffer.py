"""Extract the Charente river (BD TOPAGE) within 200 km of Saintes, buffered by 200 m.

Usage: python experiments/extract_charente_buffer.py
"""
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

SRC = Path("~/Documents/Projects/data/BD_TOPAGE/CoursEau_Topage2026.gpkg").expanduser()
DST = Path("~/Documents/Projects/data/flood-monitoring/external/charente_buffer200.gpkg").expanduser()
DST_STAC = DST.with_name("charente_buffer200_simplified.gpkg")

SAINTES = Point(-0.6333, 45.7458)  # lon, lat (WGS84)
RADIUS_M = 200_000
BUFFER_M = 200
RIVER_NAME = "la Charente"  # exact TopoOH value; excludes "Bras de la Charente", canals, etc.
METRIC_CRS = "EPSG:2154"  # Lambert-93
SIMPLIFY_M = 100  # Douglas-Peucker tolerance, keeps the polygon small enough for a STAC request


def _n_vertices(geom) -> int:
    polygons = getattr(geom, "geoms", [geom])
    return sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in polygons)


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

    # Final step: one simplified WGS84 polygon, small enough to pass as a STAC `intersects` geometry.
    merged = clipped.geometry.union_all()
    # buffer first so the simplified polygon still covers the original
    simplified = merged.buffer(SIMPLIFY_M).simplify(SIMPLIFY_M, preserve_topology=True)
    stac = gpd.GeoDataFrame(geometry=[simplified], crs=METRIC_CRS).to_crs("EPSG:4326")
    stac.to_file(DST_STAC, driver="GPKG", layer="charente_buffer200_simplified")
    print(f"simplified {_n_vertices(merged)} -> {_n_vertices(simplified)} vertices -> {DST_STAC}")


if __name__ == "__main__":
    main()
