"""Run the detection pipeline of an Event end to end: download, stack, despeckle, detect.

``python -m flood_monitoring.pipeline [event] [--force]``; the Event defaults to saintes-2026-02.
Everything comes from ``events/<event>.yaml``. Stages whose output exists are skipped, and the
download is skipped without a search when Subsets are already on disk.
"""

from __future__ import annotations

import argparse
import sys
from types import ModuleType

from .config import load_event_config
from .paths import EventPaths, event_paths

DEFAULT_EVENT = "saintes-2026-02"
# In processing order; "detect" is the event stage then the flood-extent stage.
STAGE_NAMES = ("download", "stack", "despeckle", "event", "flood-extent")


def load_stages() -> dict[str, ModuleType]:
    from . import despeckle, download, event, flood_extent, stack

    return {
        "download": download,
        "stack": stack,
        "despeckle": despeckle,
        "event": event,
        "flood-extent": flood_extent,
    }


def run_pipeline(
    event: str = DEFAULT_EVENT,
    force: bool = False,
    paths: EventPaths | None = None,
    stages: dict[str, ModuleType] | None = None,
) -> None:
    """Run every stage for *event*. Raises if the Event config is missing or a stage fails."""
    config = load_event_config(event)
    paths = paths or event_paths(event)
    stages = stages or load_stages()
    paths.ensure()

    for name in STAGE_NAMES:
        print(f"[{name}]")
        if name == "download" and not force and _has_subsets(paths):
            print(f"skip: Subsets already in {paths.raw}")
            continue
        stages[name].run(config, paths, force=force)


def _has_subsets(paths: EventPaths) -> bool:
    return any(paths.raw.glob("sentinel-1-rtc/*/*/*.tif"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flood_monitoring.pipeline", description=__doc__.splitlines()[0])
    ap.add_argument("event", nargs="?", default=DEFAULT_EVENT, help=f"Event slug (default {DEFAULT_EVENT})")
    ap.add_argument("--force", action="store_true", help="redo every stage, including the download")
    a = ap.parse_args(argv)
    try:
        run_pipeline(a.event, force=a.force)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
