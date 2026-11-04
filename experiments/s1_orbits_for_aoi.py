"""Per relative orbit: share of the AOI covered by sentinel-1-rtc footprints, and Acquisition dates.

Usage: uv run --no-project --with geopandas --with requests python experiments/s1_orbits_for_aoi.py
"""
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import mapping, shape

STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
AOI = Path("~/Documents/Projects/data/flood-monitoring/external/hydro_major_buffer5000_simplified.gpkg").expanduser()
PERIOD = "2025-01-01T00:00:00Z/2026-03-01T23:59:59Z"
METRIC_CRS = "EPSG:2154"


def search(aoi_wgs84) -> list[dict]:
    items, url = [], STAC_SEARCH
    body = {"collections": ["sentinel-1-rtc"], "intersects": mapping(aoi_wgs84), "datetime": PERIOD, "limit": 200,
            "query": {"sar:instrument_mode": {"eq": "IW"}}}
    while url:
        resp = requests.post(url, json=body, timeout=300)
        resp.raise_for_status()
        page = resp.json()
        items += page["features"]
        nxt = [link for link in page.get("links", []) if link["rel"] == "next"]
        url, body = (nxt[0]["href"], nxt[0].get("body", body)) if nxt else (None, None)
    return items


def main() -> None:
    aoi_gdf = gpd.read_file(AOI)
    aoi = aoi_gdf.to_crs(METRIC_CRS).geometry.iloc[0]
    items = search(aoi_gdf.geometry.iloc[0])
    rows = [{"orbit": f["properties"]["sat:relative_orbit"], "state": f["properties"]["sat:orbit_state"],
             "date": f["properties"]["datetime"][:10], "geometry": shape(f["geometry"])} for f in items]
    frames = gpd.GeoDataFrame(rows, crs="EPSG:4326").to_crs(METRIC_CRS)
    print(f"{len(frames)} items")
    stats = []
    for (state, orbit), g in frames.groupby(["state", "orbit"]):
        stats.append((g.union_all().intersection(aoi).area / aoi.area, state, orbit, g))
    for share, state, orbit, g in sorted(stats, key=lambda s: -s[0]):
        dates = sorted(g.date.unique())
        print(f"orbit {orbit:>3} {state:<10} share {share:6.1%}  {len(dates)} dates  {dates[0]}..{dates[-1]}  "
              f"{len(g) / len(dates):.1f} slices/date")
        print("    " + " ".join(dates))


if __name__ == "__main__":
    main()
