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
