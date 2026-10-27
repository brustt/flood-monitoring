"""Download the Series as Subsets over the Area of Interest."""

from __future__ import annotations

import planetary_computer
from eo_stac import Sentinel1Params, STACSource, download_items, search_items

from .config import EventConfig
from .paths import EventPaths

STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    """Search the Series and write each asset as a Subset under ``paths.raw``.

    Files already on disk are not fetched again unless *force* is set. The
    search-result metadata is written by eo-stac to ``<raw>/metadata/``.
    """
    series = config.series
    if len(series.relative_orbits) > 1:
        raise NotImplementedError("downloading several relative orbits is not implemented yet (see #23)")
    gdf = search_items(
        STACSource.from_url(STAC_API),
        collections=[series.collection],
        bbox=list(config.area_of_interest),
        datetime=f"{series.start.isoformat()}/{series.end.isoformat()}",
        params=Sentinel1Params(
            orbit_state=series.orbit_state,
            instrument_mode=series.instrument_mode,
            orbit_number=series.relative_orbits[0],
            polarizations=[a.upper() for a in series.assets],
        ),
    )
    if gdf.empty:
        raise RuntimeError(f"no {series.collection} items found for Event {config.event!r}")

    gdf = download_items(
        gdf,
        paths.raw,
        assets=list(series.assets),
        bbox=list(config.area_of_interest),
        sign=planetary_computer.sign,
        overwrite=force,
    )
    missing = [
        row.item_id
        for row in gdf.itertuples()
        if set(row.local_paths) != set(series.assets)
    ]
    if missing:
        raise RuntimeError(f"{len(missing)} item(s) missing assets after download: {missing}")
    print(f"{len(gdf)} Acquisitions in {paths.raw}")
