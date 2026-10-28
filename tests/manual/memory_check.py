"""Memory gate of the xarray/dask rewrite (docs/xarray-dask-rewrite.md): peak RSS of each stage on large inputs.

    uv run python tests/manual/memory_check.py run <scratch dir> [--budget 256] [--rebuild] [--out <json>]

Each stage runs in its own child process (``... child <stage> <root> <budget> <kind>``). A stage passes when its
peak stays under the baseline (largest peak on the small inputs) plus 1.5 x the budget. Not collected by pytest.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reference_inputs as ri  # noqa: E402

STAGES = (("check_cube", "single"), ("event", "single"), ("flood-extent", "single"), ("composite", "single"),
          ("merge", "merge"))
BIG = 4000
BASE = 64


def build(scratch: Path, rebuild: bool) -> None:
    """Write the four input roots unless present."""
    for name, size, chunk in (("big", BIG, 288), ("base", BASE, 16)):
        single, merge = scratch / f"{name}-single", scratch / f"{name}-merge"
        if rebuild:
            shutil.rmtree(single, ignore_errors=True)
            shutil.rmtree(merge, ignore_errors=True)
        if not ri.paths(single).filtered_cube.exists():
            ri.single_series(single, size=size, dates=12, chunk=chunk, flood_from=9, flood_fraction=0.3,
                             salt_fraction=0.02)
        if not ri.paths(merge).series_paths(110).summary.exists():
            ri.two_series(merge, size=size)


def child(stage: str, root: Path, budget: float, kind: str) -> None:
    from flood_monitoring import composite, event, flood_extent, merge_stage
    from flood_monitoring.cube import check_cube

    if kind == "single":
        size = BIG if root.name.startswith("big") else BASE
        config, paths = ri.single_series_config(root, size=size, dates=12, budget_mb=budget)
    else:
        config, paths = ri.two_series_config(root, size=BIG if root.name.startswith("big") else BASE, budget_mb=budget)
    if stage == "check_cube":
        try:
            from flood_monitoring import chunks
        except ImportError:  # the code before the rewrite
            import xarray as xr

            with xr.open_dataset(paths.filtered_cube) as ds:
                check_cube(ds, nodata=ds.attrs.get("nodata"))
        else:
            ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=10)
            with ds, chunks.scheduler(plan.workers, budget):
                check_cube(ds, nodata=ds.attrs.get("nodata"))
    else:
        module = {"event": event, "flood-extent": flood_extent, "composite": composite, "merge": merge_stage}[stage]
        module.run(config, paths, force=True)
    print(json.dumps({"stage": stage, "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}))


def measure(stage: str, root: Path, budget: float, kind: str) -> dict:
    from flood_monitoring._env import clean_environment

    proc = subprocess.run(
        [sys.executable, __file__, "child", stage, str(root), str(budget), kind],
        env=clean_environment(dict(os.environ)), capture_output=True, text=True,
    )
    rss = None
    for line in proc.stdout.splitlines():
        if line.startswith("{"):
            rss = json.loads(line)["max_rss_mb"]
    if proc.returncode != 0 or rss is None:
        print(f"{stage} on {root.name} failed ({proc.returncode}):\n{proc.stderr[-2000:]}", file=sys.stderr)
        rss = None
    return {"stage": stage, "max_rss_mb": rss, "returncode": proc.returncode}


def run(scratch: Path, budget: float, rebuild: bool, out: Path | None) -> int:
    scratch.mkdir(parents=True, exist_ok=True)
    # In a child: Linux carries a process's peak RSS over to the children it forks, so this one must stay small.
    subprocess.run([sys.executable, __file__, "build", str(scratch), *(["--rebuild"] if rebuild else [])], check=True)
    base = [measure(stage, scratch / f"base-{kind}", budget, kind) for stage, kind in STAGES]
    if any(r["max_rss_mb"] is None for r in base):
        print("a stage failed on the small inputs; no baseline", file=sys.stderr)
        return 1
    baseline = max(r["max_rss_mb"] for r in base)
    threshold = baseline + 1.5 * budget
    stages = []
    for stage, kind in STAGES:
        r = measure(stage, scratch / f"big-{kind}", budget, kind)
        r["pass"] = r["max_rss_mb"] is not None and r["max_rss_mb"] <= threshold
        stages.append(r)

    print(f"budget {budget:g} MB, baseline {baseline:.0f} MB, threshold {threshold:.0f} MB")
    print(f"{'stage':<14}{'peak MB':>10}{'threshold':>11}  result")
    for r in stages:
        peak = "-" if r["max_rss_mb"] is None else f"{r['max_rss_mb']:.0f}"
        print(f"{r['stage']:<14}{peak:>10}{threshold:>11.0f}  {'pass' if r['pass'] else 'FAIL'}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        result = {"budget_mb": budget, "baseline_mb": baseline, "threshold_mb": threshold, "stages": stages}
        out.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if all(r["pass"] for r in stages) else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="memory_check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("run")
    rp.add_argument("scratch", type=Path)
    rp.add_argument("--budget", type=float, default=256.0)
    rp.add_argument("--rebuild", action="store_true")
    rp.add_argument("--out", type=Path)
    cp = sub.add_parser("child")
    cp.add_argument("stage")
    cp.add_argument("root", type=Path)
    cp.add_argument("budget", type=float)
    cp.add_argument("kind", choices=("single", "merge"))
    bp = sub.add_parser("build")
    bp.add_argument("scratch", type=Path)
    bp.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.scratch, a.rebuild)
        return 0
    if a.cmd == "child":
        child(a.stage, a.root, a.budget, a.kind)
        return 0
    return run(a.scratch, a.budget, a.rebuild, a.out)


if __name__ == "__main__":
    sys.exit(main())
