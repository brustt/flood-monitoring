"""One command runs the pipeline of an Event; ``--stage`` runs a single stage.

``uv run src/flood_monitoring/cli.py run [event]`` works too; the Event defaults to saintes-2026-02.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import replace
from types import ModuleType

from flood_monitoring import composite, despeckle, download, event, export, flood_extent, merge_stage, stack
from flood_monitoring._env import ensure_clean_environment
from flood_monitoring.config import EventConfig, load_event_config
from flood_monitoring.paths import EventPaths, event_paths

DEFAULT_EVENT = "saintes-2026-02"

# In processing order. Stages up to "composite" run once per Series (relative orbit).
PER_SERIES = ("download", "stack", "despeckle", "event", "flood-extent", "composite")
STAGES = {
    "download": download,
    "stack": stack,
    "despeckle": despeckle,
    "event": event,
    "flood-extent": flood_extent,
    "composite": composite,
    "merge": merge_stage,
    "export": export,
}


# What a per-series stage consumes and what it produces: the consumed folder or file is deleted once the
# produced one exists, unless --keep-intermediates.
INTERMEDIATES = {
    "stack": (lambda p: p.raw, lambda p: p.cube),
    "despeckle": (lambda p: p.cube, lambda p: p.filtered_cube),
    "composite": (lambda p: p.filtered_cube, lambda p: p.after_composite),
}
# The output that shows a stage is done, whether or not its input was deleted since.
DONE = {
    "stack": lambda p: p.cube,
    "despeckle": lambda p: p.filtered_cube,
    "composite": lambda p: p.before_composite,
}


def _release(name: str, paths: EventPaths) -> None:
    """Delete what stage *name* consumed, once what it produced exists."""
    if name not in INTERMEDIATES:
        return
    consumed, produced = (get(paths) for get in INTERMEDIATES[name])
    if produced.exists() and consumed.exists():
        if consumed.is_dir():
            shutil.rmtree(consumed)
        else:
            consumed.unlink()
        print(f"removed {consumed}")


def _superseded(name: str, later: list[str], paths: EventPaths) -> bool:
    """Whether a stage up to the despeckle is skipped because a later requested stage is already done.

    Its input is deleted by default, so running it again would only rebuild what the later stage no longer needs.
    """
    return name in ("download", "stack", "despeckle") and any(n in DONE and DONE[n](paths).exists() for n in later)


def run_stages(
    config: EventConfig,
    paths: EventPaths,
    names: list[str],
    force: bool = False,
    stages: dict[str, ModuleType] | None = None,
    keep_intermediates: bool = False,
) -> None:
    """Run *names* in order. Stages whose output exists skip themselves; the download
    searches the Series and eo-stac skips the Subsets already on disk, so a partial
    download is resumed.

    Consecutive per-series stages run one orbit at a time, so only one orbit's intermediates exist at once. The
    raw Subsets, the Cube and the Filtered Cube are deleted once the next stage has produced its result, unless
    *keep_intermediates*.
    """
    stages = stages or STAGES
    orbits = config.series.relative_orbits
    # A single orbit keeps the flat folders: its results already are the global Layers.
    series_paths = [paths] if len(orbits) == 1 else [paths.series_paths(o) for o in orbits]
    paths.ensure()
    for p in series_paths:
        p.ensure()
    i = 0
    while i < len(names):
        if names[i] not in PER_SERIES:
            print(f"[{names[i]}]")
            stages[names[i]].run(config, paths, force=force)
            i += 1
            continue
        j = i
        while j < len(names) and names[j] in PER_SERIES:
            j += 1
        group = names[i:j]
        for orbit, p in zip(orbits, series_paths):
            series_config = replace(config, series=replace(config.series, relative_orbits=(orbit,)))
            for k, name in enumerate(group):
                label = f"[{name}]" if len(orbits) == 1 else f"[{name}] relative orbit {orbit}"
                if not force and _superseded(name, group[k + 1 :], p):
                    print(f"{label} skip: a later stage is done")
                    continue
                print(label)
                stages[name].run(series_config, p, force=force)
                if not keep_intermediates:
                    _release(name, p)
        i = j


def main(argv: list[str] | None = None) -> int:
    ensure_clean_environment(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="flood-monitoring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("run", help="run the pipeline of an Event, skipping stages already done")
    rp.add_argument("event", nargs="?", default=DEFAULT_EVENT, help=f"Event slug (default {DEFAULT_EVENT})")
    rp.add_argument("--stage", choices=list(STAGES), help="run this stage only")
    rp.add_argument("--force", action="store_true", help="redo stages whose output exists, download included")
    rp.add_argument(
        "--keep-intermediates", action="store_true",
        help="keep the raw Subsets, the Cube and the Filtered Cube (deleted by default once the next stage has run)",
    )
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
        run_stages(config, paths, names, force=a.force, keep_intermediates=a.keep_intermediates)
    except (NotImplementedError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _show(config, paths) -> None:
    series = config.series
    print(f"Project           {config.project}")
    print(f"Event             {config.event}")
    form = "polygon, bounding box" if config.aoi_polygon is not None else "rectangle"
    print(f"Area of Interest  {list(config.area_of_interest)} ({form})")
    print(f"Window            {config.window.start} to {config.window.end}")
    print(
        f"Series            {series.collection}, {series.start} to {series.end}, "
        f"{series.orbit_state}, {series.instrument_mode}, relative orbit(s) {', '.join(map(str, series.relative_orbits))}, "
        f"assets {', '.join(series.assets)}"
    )
    grid = config.event_grid
    print(f"Event grid        {grid.crs or 'CRS of the first Series'}, {grid.pixel_size:g} m")
    print("Paths")
    for name, path in paths.describe().items():
        print(f"  {name:<10} {path}")


if __name__ == "__main__":
    sys.exit(main())
