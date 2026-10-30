# flood-monitoring

Maps the extent of a flood from a time series of Sentinel-1 radar images and prepares the result for the brustt.xyz Platform.

Open water reflects radar away, so flooded pixels turn dark. The pipeline downloads the Sentinel-1 passes over an Area of Interest, removes speckle, detects when the flood starts, classifies water on every date since, and subtracts permanent water. The result is a Flood Extent raster plus before/after radar pictures, exported as COGs with a manifest for the Platform.

## Quick start

```bash
uv sync
uv run flood-monitoring show saintes-2026-02             # config and resolved paths
uv run flood-monitoring run saintes-2026-02              # download to export
uv run flood-monitoring run saintes-2026-02 --stage event # one stage
uv run flood-monitoring run saintes-2026-02 --force       # redo stages whose output exists
uv run pytest
```

Events are described in `events/<event>.yaml`. Data is kept outside the repo under `$FLOOD_MONITORING_DATA_ROOT` (default `~/Documents/Projects/data/flood-monitoring`); final Layers go to `outputs/<event>/` and the Platform manifest to `publish/`.

## Pipeline

`download` → `stack` → `despeckle` → `event` → `flood-extent` → `composite` → `merge` (multi-orbit only) → `export`

## Documentation

- [`docs/overview.md`](docs/overview.md): the workflow, configuration, skip/resume rules, file layout.
- Stage by stage: [ingest](docs/pipeline/1-ingest.md) (download, stack, despeckle), [detection](docs/pipeline/2-detection.md) (event, flood-extent), [composite](docs/pipeline/3-composite.md), [merge](docs/pipeline/4-merge.md), [export](docs/pipeline/5-export.md).
- [`CONTEXT.md`](CONTEXT.md): glossary (Event, Series, Cube, Baseline, Drop, Flood Extent…).
- [`docs/detection-rules.md`](docs/detection-rules.md) and [`docs/adr/`](docs/adr/): why the rules and the multi-orbit design are what they are.

## Configuration

`processing.memory_budget_mb` in the Event config (default `4096`) bounds the memory of every stage after download:

```yaml
processing:
  memory_budget_mb: 2048
```

- `stack` and `despeckle` pass it to eo-pipe (`stack_tiles`, `lee_filter`, `quegan_filter`).
- `event`, `flood-extent`, `composite` and `merge` read the Cube (or the warped Series rasters) in square spatial chunks, the whole time axis per chunk. Each prints `chunks: <side> px, <workers> at a time`. The side is planned from the budget, net of two GDAL block caches (rasterio's and the system GDAL's, `max(32, budget / 16)` MB each) and the whole-grid Area of Interest mask (1 byte per pixel), then shrunk to whole multiples (or divisors) of the Cube's stored chunks.
- A budget too small for one 16 px chunk stops the stage with `memory_budget_mb=<value> is too small … set memory_budget_mb to at least <n>`.

Scratch disk, uncompressed: `flood-extent` needs `2 x (post-event dates + 1) x height x width` bytes while sieving, then half of it (the sieved masks become the Water Masks and Permanent Water); `merge` needs `26 x Series x height x width` bytes on the Event grid for the warped rasters (`processed/<event>/_merge/`, removed afterwards).

Each of `event`, `flood-extent`, `composite` and `merge` first deletes its own outputs, then writes rasters as `*.partial` files renamed on success and `summary.json` atomically, so a stage killed midway is redone by the next run. Re-running an upstream stage does not invalidate later ones: run them again with `--force`.

## GDAL/PROJ environment

Shells that export OTB's `PROJ_LIB`, `GDAL_DATA`, `LD_LIBRARY_PATH`, `GDAL_DRIVER_PATH` or `PYTHONPATH` break GDAL here (`proj.db contains DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 6 is expected`). The fix lives in the CLI entry point (`src/flood_monitoring/_env.py`): before any stage runs, `flood-monitoring` re-executes itself once with `PYTHONPATH`, `LD_LIBRARY_PATH`, `GDAL_DRIVER_PATH` and `PROJ_LIB` removed, `PROJ_DATA` set to the venv's `pyproj/proj_dir/share/proj` and `GDAL_DATA` set to `gdal-config --datadir`. It must be a re-exec because `LD_LIBRARY_PATH` is read by the dynamic loader at process start. No manual overrides are needed.
