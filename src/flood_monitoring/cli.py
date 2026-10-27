"""One command runs the pipeline of an Event; ``--stage`` runs a single stage.

``uv run src/flood_monitoring/cli.py run [event]`` works too; the Event defaults to saintes-2026-02.
"""

from __future__ import annotations

import argparse
import sys
from types import ModuleType

from flood_monitoring import composite, despeckle, download, event, export, flood_extent, stack
from flood_monitoring.config import EventConfig, load_event_config
from flood_monitoring.paths import EventPaths, event_paths

DEFAULT_EVENT = "saintes-2026-02"

# In processing order.
STAGES = {
    "download": download,
    "stack": stack,
    "despeckle": despeckle,
    "event": event,
    "flood-extent": flood_extent,
    "composite": composite,
    "export": export,
}


def has_subsets(paths: EventPaths) -> bool:
    return any(paths.raw.glob("sentinel-1-rtc/*/*/*.tif"))


def run_stages(
    config: EventConfig,
    paths: EventPaths,
    names: list[str],
    force: bool = False,
    stages: dict[str, ModuleType] | None = None,
) -> None:
    """Run *names* in order. Stages whose output exists skip themselves; the download is
    skipped without a search when Subsets are already on disk."""
    stages = stages or STAGES
    paths.ensure()
    for name in names:
        print(f"[{name}]")
        if name == "download" and not force and has_subsets(paths):
            print(f"skip: Subsets already in {paths.raw}")
            continue
        stages[name].run(config, paths, force=force)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flood-monitoring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("run", help="run the pipeline of an Event, skipping stages already done")
    rp.add_argument("event", nargs="?", default=DEFAULT_EVENT, help=f"Event slug (default {DEFAULT_EVENT})")
    rp.add_argument("--stage", choices=list(STAGES), help="run this stage only")
    rp.add_argument("--force", action="store_true", help="redo stages whose output exists, download included")
    sp = sub.add_parser("show", help="print the config and resolved paths of an Event")
    sp.add_argument("event", nargs="?", default=DEFAULT_EVENT, help=f"Event slug (default {DEFAULT_EVENT})")
    a = ap.parse_args(argv)

    try:
        config = load_event_config(a.event)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    paths = event_paths(a.event)

    if a.cmd == "show":
        _show(config, paths)
        return 0

    names = [a.stage] if a.stage else list(STAGES)
    try:
        run_stages(config, paths, names, force=a.force)
    except (NotImplementedError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _show(config, paths) -> None:
    series = config.series
    print(f"Project           {config.project}")
    print(f"Event             {config.event}")
    form = "river buffer, bounding box" if config.aoi_polygon is not None else "rectangle"
    print(f"Area of Interest  {list(config.area_of_interest)} ({form})")
    print(f"Window            {config.window.start} to {config.window.end}")
    print(
        f"Series            {series.collection}, {series.start} to {series.end}, "
        f"{series.orbit_state}, {series.instrument_mode}, relative orbit(s) {', '.join(map(str, series.relative_orbits))}, "
        f"assets {', '.join(series.assets)}"
    )
    print("Paths")
    for name, path in paths.describe().items():
        print(f"  {name:<10} {path}")


if __name__ == "__main__":
    sys.exit(main())
