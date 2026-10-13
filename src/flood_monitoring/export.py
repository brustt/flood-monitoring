"""Write the Layers as COGs, the Publish manifests, validate and archive."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

import rasterio
import rasterio.shutil
import yaml
from platform_publish import validate

from .config import EventConfig
from .paths import EventPaths

FLOOD_EXTENT_NODATA = 255
FLOOD_COLOR = "#1f78b4"
COPERNICUS = "Contains modified Copernicus Sentinel data {year}"


def write_cog(src: Path, dst: Path, categorical: bool, nodata: float | None = None) -> None:
    """Copy *src* to *dst* as a tiled COG in its own CRS with internal overviews.

    Categorical rasters get nearest overviews, continuous ones average. *nodata* overrides the
    value declared in *src*.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dst.parent) as tmp:
        source = Path(tmp) / "source.tif"
        shutil.copyfile(src, source)
        if nodata is not None:
            with rasterio.open(source, "r+") as ds:
                ds.nodata = nodata
        rasterio.shutil.copy(
            source,
            dst,
            driver="COG",
            BLOCKSIZE=256,
            OVERVIEW_RESAMPLING="NEAREST" if categorical else "AVERAGE",
        )


def event_title(event: str) -> str:
    """``saintes-2026-02`` -> ``Saintes flood, February 2026``."""
    *place, year, month = event.split("-")
    return f"{' '.join(place).title()} flood, {date(int(year), int(month), 1):%B %Y}"


def event_manifest(config: EventConfig, summary: dict, layers_dir: str | None = None) -> dict:
    """The ``<event>.publish.yaml`` content. *layers_dir* is where the COGs are, relative to the manifest."""
    layers_dir = layers_dir or f"../outputs/{config.event}"
    r = config.composite
    rgb = {"bands": [1, 2, 3], "ranges": [list(r.vv), list(r.vh), list(r.vv_minus_vh)]}

    def imagery(slug: str, title: str, day: str) -> dict:
        return {
            "slug": slug,
            "file": f"{layers_dir}/{slug}.tif",
            "role": "imagery",
            "kind": "raster",
            "title": title,
            "shown": False,
            "acquired": day,
            "attribution": COPERNICUS.format(year=day[:4]),
            "rgb": {"bands": list(rgb["bands"]), "ranges": [list(x) for x in rgb["ranges"]]},
        }

    return {
        "event": config.event,
        "title": event_title(config.event),
        "start": config.window.start.isoformat(),
        "end": config.window.end.isoformat(),
        "compare": ["s1-before", "s1-after"],
        "layers": [
            {
                "slug": "flood-extent",
                "file": f"{layers_dir}/flood-extent.tif",
                "role": "result",
                "kind": "raster",
                "title": "Flood extent",
                "shown": True,
                "categories": [
                    {"value": 0, "color": "#000000", "label": "No flood", "transparent": True},
                    {"value": 1, "color": FLOOD_COLOR, "label": "Flooded"},
                ],
            },
            imagery("s1-before", "Sentinel-1 before the flood", summary["before"]),
            imagery("s1-after", "Sentinel-1 at the flood peak", summary["peak"]),
        ],
    }


def run(
    config: EventConfig, paths: EventPaths, force: bool = False, executed_at: datetime | None = None
) -> None:
    """Always rewrites the outputs: they are small and must match the processed rasters."""
    executed_at = executed_at or datetime.now()
    sources = {
        "flood-extent": (paths.flood_extent, "flood-extent"),
        "s1-before": (paths.before_composite, "composite"),
        "s1-after": (paths.after_composite, "composite"),
    }
    for source, stage in sources.values():
        if not source.exists():
            raise FileNotFoundError(f"{source} is missing; run the {stage} stage first")
    if not paths.summary.exists():
        raise FileNotFoundError(f"{paths.summary} is missing; run the flood-extent stage first")
    summary = json.loads(paths.summary.read_text())
    if "before" not in summary or "peak" not in summary:
        raise FileNotFoundError(f"{paths.summary} has no before/peak dates; run the flood-extent stage first")
    project_yaml = paths.publish / "project.yaml"
    if not project_yaml.exists():
        raise FileNotFoundError(f"{project_yaml} is missing; write it once by hand")

    paths.outputs.mkdir(parents=True, exist_ok=True)
    for slug, (source, _) in sources.items():
        target = paths.outputs / f"{slug}.tif"
        if slug == "flood-extent":
            write_cog(source, target, categorical=True, nodata=FLOOD_EXTENT_NODATA)
        else:
            write_cog(source, target, categorical=False)
        print(f"{slug}: {target} ({target.stat().st_size / 1e6:.2f} MB)")

    event_yaml = paths.publish / f"{config.event}.publish.yaml"
    layers_dir = Path(os.path.relpath(paths.outputs, paths.publish)).as_posix()
    manifest = event_manifest(config, summary, layers_dir)
    event_yaml.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    print(f"manifest: {event_yaml}")

    report = validate(project_yaml, event_yaml)
    if not report.ok:
        raise RuntimeError("publish validation failed:\n" + "\n".join(f"  {e}" for e in report.errors))
    print("publish validation: ok")

    archive = paths.archive(executed_at)
    shutil.copytree(paths.outputs, archive, dirs_exist_ok=True)
    print(f"archive: {archive}")
