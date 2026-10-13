"""Stack the downloaded Subsets into the Cube."""

from __future__ import annotations

import shutil
from pathlib import Path

import xarray as xr
from eo_pipe.pipeline import MergeBatch, PipelineComposition

from .config import EventConfig
from .cube import check_cube
from .paths import EventPaths


def subsets(raw: Path) -> list[Path]:
    """Every raw Subset of the Series, in a stable order."""
    return sorted(raw.glob("sentinel-1-rtc/*/*/*.tif"))


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    if paths.cube.exists() and not force:
        print(f"skip: {paths.cube} exists")
        return
    inputs = subsets(paths.raw)
    if not inputs:
        raise FileNotFoundError(f"no Subsets in {paths.raw}; run the download stage first")

    # Native grid, no align_to_tile: the Subsets of one orbit already share a lattice.
    work = paths.interim / "_stack"
    shutil.rmtree(work, ignore_errors=True)
    ctx = (
        PipelineComposition(workspace=work)
        .add_step("stack_tiles", MergeBatch(), group=config.event)
        .run(inputs, save_intermediate=True)
    )
    built = ctx.inputs[0]
    shutil.move(built, paths.cube)
    shutil.rmtree(work, ignore_errors=True)

    with xr.open_dataset(paths.cube) as ds:
        acquisitions = len({p.parent.name for p in inputs})
        check_cube(ds, nodata=ds.attrs.get("nodata"), expected=acquisitions)
        print(f"cube: {paths.cube} ({ds.sizes['time']} Acquisitions, variables {list(ds.data_vars)})")
