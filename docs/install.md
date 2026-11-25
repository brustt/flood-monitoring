# Install

Two installs, from the same `pyproject.toml` and `uv.lock`:

| Install | Command | Runs | Use |
|---|---|---|---|
| **Full** (default) | `uv sync` | every stage | the home machine |
| **Light** | `uv sync --frozen --no-dev --no-group full` | `load` only | a machine close to the data, such as the VPS |

The light set is the package's own dependencies: ready-made wheels only (odc-stac, rasterio with its bundled GDAL,
xarray, dask, netCDF4…). The full install adds the `full` dependency group (eo-pipe, eo-stac, platform-publish), which
`uv sync` installs by default (`default-groups` in `pyproject.toml`). A plain `pip install .` gives the light set, as
pip ignores dependency groups; it is not enough on its own, though, because the Event configs are read from the
clone's `events/` folder.

## Full install

Needs, besides Python 3.12 and [uv](https://docs.astral.sh/uv/):

- **The system GDAL C library** (3.12.2 in `/usr/local`, `gdal-config` on the `PATH`). eo-pipe pins the GDAL Python
  bindings, which are compiled against it at install time; `pyproject.toml` (`[tool.uv.extra-build-*]`) adds numpy as
  a build dependency and links against `/usr/local/lib`.
- **An eo-pipe checkout next to this repo**, at `../eo-pipe` (editable path source).
- Network access to GitHub, where eo-stac and platform-publish are fetched from.

```bash
uv sync
uv run pytest
```

The CLI cleans the GDAL/PROJ environment of the shell before running (see the README, "GDAL/PROJ environment").

## Light install: the load stage on another machine

`load` is network-bound: Planetary Computer reads at ~5 MB/s from home and ~45 MB/s from the OVH VPS (#42,
`experiments/pc_speed.sh`, which needs only `curl`, `jq` and `bc`). It runs on the light install, with no eo-pipe,
eo-stac, platform-publish or system GDAL, so nothing is compiled. Build the Cubes there and copy them home, where the
other stages run:

```bash
# on the VPS, once: uv, the repo, the light install
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone -b dev https://github.com/brustt/flood-monitoring.git && cd flood-monitoring
uv sync --frozen --no-dev --no-group full
# a polygon Area of Interest is a file outside the repo: copy it to the same relative path, e.g. from home
#   rsync -P ~/Documents/Projects/data/flood-monitoring/external/hydro_major_buffer5000_simplified.gpkg \
#            vps:~/data/flood-monitoring/external/
# lower processing.memory_budget_mb in events/<event>.yaml below the VPS's free memory (e.g. 3072 on 8 GB)

# in tmux, off-peak: the VPS also serves the Platform
FLOOD_MONITORING_DATA_ROOT=~/data/flood-monitoring nice -n 19 .venv/bin/flood-monitoring run <event> --stage load

# at home: copy the Cubes (every orbit's interim/ folder), then run the rest; load skips the Cubes that exist
rsync -P -r vps:~/data/flood-monitoring/interim/<event>/ ~/Documents/Projects/data/flood-monitoring/interim/<event>/
uv run flood-monitoring run <event>
```

- `--frozen` installs exactly `uv.lock` without resolving again, which would need `../eo-pipe`.
- Call `.venv/bin/flood-monitoring` (or `uv run --no-sync`), not `uv run`: `uv run` syncs the default groups and would
  try to install the full set.
- Any other stage on the light install stops with `the <stage> stage needs the full install`.
- Measured on `saintes-2026-02` (#43): a 602 MB environment, `load` in 7 min 15 s at home, 1.4 GB peak memory.
