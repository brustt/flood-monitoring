# Stages 1-2: load, despeckle (and download, stack)

These stages turn catalogue entries into a clean, analysis-ready **Filtered Cube**. Nothing here decides what is flooded.

## The Cube

All Acquisitions of one Series as one NetCDF4 file (`interim/<event>/cube.nc`) with a `time` axis and one variable per polarization (`VV`, `VH`), on the native grid of the orbit.

- **Stored as int16 hundredths of a dB** (`scale_factor` 0.01, nodata -32768, zlib level 1 with shuffle) and read by xarray as float32 dB with NaN for nodata; rounding is 0.005 dB (`cube.py`). Only the speckle filters need linear power: they convert per chunk, in memory.
- **Chunked by space, the whole time axis per chunk**, the layout the temporal filter and the per-pixel Baseline need.
- **With a polygon Area of Interest, only the chunks that touch it are stored**, plus a margin for the halo of the speckle filters (`config.halo_margin`: Lee window / 2 + Quegan window / 2), so no filtered pixel of the polygon reads a skipped chunk. The others take no space and read back as nodata.
- `check_cube` verifies one time step per Acquisition, in increasing order, each variable holding at least one valid pixel on every date.

## 1. `load` (`load.py`)

Builds the Cube straight from the Sentinel-1 RTC COGs of the Microsoft Planetary Computer (`sentinel-1-rtc`, radiometrically terrain-corrected) with odc-stac: no raw Subsets and no float32 temporary stack on disk.

1. **Search** with pystac-client, per Series: collection, the polygon (or the bounding box), the Series dates, `orbit_state`, `instrument_mode`, the relative orbit. The Items are saved as `interim/<event>/items.parquet` (stac-geoparquet). No item found is an error.
2. **Grid**: the native CRS of the orbit (`proj:code`), 10 m, the bounding box snapped to the native lattice. Frames of one pass are merged into one step per day (`groupby="solar_day"`); each step is stamped with the earliest acquisition start of its day, to the second.
3. **Chunks** of at least 512 px, the COG block side (a smaller chunk fetches each block several times), at most 2 at a time: on a saturated link more chunks only queue (#42, #43).
4. **Reads**: each chunk is one odc-stac load of that chunk's grid, with `processing.io_threads` COG reads in flight (default 8, the plateau of a ~5 MB/s link). The URLs are signed at each chunk read, so a run of many hours outlives the tokens. GDAL retries transient HTTP errors; a chunk that still fails is retried 3 times (10, 20, 40 s) before the stage fails.
5. Each chunk is converted to dB and written into the Cube (`cube.write_db_cube`), as `cube.nc.part` renamed once complete. Progress: chunks done, MB received by the host, MB/s and the time left.

Skips when `cube.nc` exists. It runs on the light install (no eo-pipe, eo-stac or system GDAL, [install](../install.md)), so it can run on a machine close to the data, such as the VPS, and the Cube be copied home.

On `saintes-2026-02` (#43): bit-identical to the Cube of `download` + `stack`, about 7 min at home (~5 MB/s). Each COG block is still fetched about 4 times, because the chunk grid does not line up with each slice's own block grid; fetching each block once is open (#42).

## `download` + `stack` (out of the default run)

The former way to the same Cube, kept through `--stage download` and `--stage stack` until `load` is validated on Charente.

- **`download`** (`download.py`) searches the same catalogue with eo-stac and writes each asset as a **Subset**, cropped to the Area of Interest's bounding box, under `raw/<event>/`. Every item must hold every requested asset. Files already on disk are not fetched again unless `--force`, so an interrupted download resumes. Several relative orbits at once raise `NotImplementedError` (#23); the CLI's per-orbit loop works around it.
- **`stack`** (`stack.py`) keeps the Subsets within the Series dates and assets, merges them with eo-pipe's `stack_tiles` on their native grid, padded to the union, one step per calendar day (`time_bin="day"`), only the chunks of the polygon window. The float32 stack is then converted to the int16 dB Cube and deleted.

`raw/` is deleted once the Cube exists, unless `--keep-intermediates`.

## 2. `despeckle` (`despeckle.py`)

Radar images carry multiplicative speckle noise. Two filters are applied in sequence **on linear power**, each chunk read with its halo so the result does not depend on the chunking:

1. **Lee** (spatial, `lee_window`, `enl`);
2. **Quegan** (temporal, `quegan_window`).

`enl` 4.4 is eo-pipe's default for Sentinel-1 IW GRDH. The kernels are eo-pipe's (`lee_block`, `quegan_block`, run lazily per chunk by `filter_lazily`); the stage converts each chunk from dB to linear power, filters it and converts back. Nodata is left out of every local statistic and stays nodata. The Quegan filter needs at least 2 Acquisitions.

With a polygon, only the chunks that touch it are filtered and stored; `window.npz` records them (`window`: chunks written, `side`: chunk side in pixels) for the later stages. The chunks follow the Cube's stored chunks (512 px from `load`, 256 from `stack`), which changes the float32 rounding of a few pixels by 0.01 dB between the two.

The output is `interim/<event>/filtered-cube.nc`, stored like the Cube and checked with `check_cube` against the Cube's number of time steps. See `docs/despeckle-before-after.png` for the effect. `cube.nc` is deleted once the Filtered Cube exists, unless `--keep-intermediates`.

Skips when `filtered-cube.nc` exists; fails with `… is missing; run the stack stage first` if there is no Cube (build it with `load`).

## Memory

`processing.memory_budget_mb` sets the chunk side and concurrency of each stage (`chunks.plan`), net of the two GDAL block caches and the whole-grid Area of Interest mask. See the README's Configuration section.
