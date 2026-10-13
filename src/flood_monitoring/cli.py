"""One command runs the pipeline of an Event; ``--stage`` runs a single stage."""

from __future__ import annotations

import argparse
import sys

from . import composite, despeckle, download, event, export, flood_extent, stack
from .config import load_event_config
from .paths import event_paths

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flood-monitoring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("run", help="run the pipeline of an Event, skipping stages already done")
    rp.add_argument("event", help="Event slug, e.g. saintes-2026-02")
    rp.add_argument("--stage", choices=list(STAGES), help="run this stage only")
    rp.add_argument("--force", action="store_true", help="redo stages whose output exists")
    sp = sub.add_parser("show", help="print the config and resolved paths of an Event")
    sp.add_argument("event", help="Event slug, e.g. saintes-2026-02")
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

    paths.ensure()
    names = [a.stage] if a.stage else list(STAGES)
    for name in names:
        print(f"[{name}]")
        try:
            STAGES[name].run(config, paths, force=a.force)
        except NotImplementedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


def _show(config, paths) -> None:
    series = config.series
    print(f"Project           {config.project}")
    print(f"Event             {config.event}")
    print(f"Area of Interest  {list(config.area_of_interest)}")
    print(f"Window            {config.window.start} to {config.window.end}")
    print(
        f"Series            {series.collection}, {series.start} to {series.end}, "
        f"{series.orbit_state}, {series.instrument_mode}, relative orbit {series.relative_orbit}, "
        f"assets {', '.join(series.assets)}"
    )
    print("Paths")
    for name, path in paths.describe().items():
        print(f"  {name:<10} {path}")


if __name__ == "__main__":
    sys.exit(main())
