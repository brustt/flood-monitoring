# Design: processing steps on xarray and dask, bounded memory (#26)

Status: design, revision 5 (pipeline review: `to_db_lazy` keeps the CRS, chunk side aligned to the Cube's stored chunks, uint8 merge encodings, manual checks added; revision 4 — review fixes: merge AOI local renamed, both GDAL caches and the AOI mask reserved from the budget, budget error quotes the configured value, batched Event Start with early stop, whole-grid AOI mask, sieve transform). Base: `main` at `ec7d84f` (the #23 multi-orbit code — `merge.py`, `merge_stage.py`, per-orbit `EventPaths`, ADR 0001 — and the CLI re-exec fix, merged via #27). Every file and function named below is as on that base.

## Why

GH #26. The stages after despeckle load the whole Cube (or whole Event-grid rasters of every Series) into numpy. For the Charente buffer (about 153 Mpx, 60 dates, VV and VH, float32) one variable is about 37 GB; `event` peaks near 110 GB, `flood-extent` near 100 GB, `merge` at 12–20 GB (estimates in the issue). The Project owner wants the full Charente buffer to process on a laptop (issue user stories 1–4, 6, 7, 10) with published outputs unchanged (story 8) and kernels still testable on small synthetic arrays (story 9).

## Scope

In:

1. One memory-budget setting, `processing.memory_budget_mb` in the Event config (default 4096, eo-pipe's default), which sets the spatial chunk size and GDAL's block cache of every stage after download and is also passed to eo-pipe's `stack_tiles`, `lee_filter` and `quegan_filter` (story 10).
2. `check_cube` (end of `stack` and `despeckle`) on a lazily opened, chunked Cube (story 2).
3. `event`: Event Start from per-chunk partial counts combined into one share per candidate date, candidates evaluated in batches with an early stop after the batch holding the Event Start (story 3).
4. `flood-extent`: lazy dB, Water Masks, Permanent Water, Flood Extent, flooded area per date and Peak; masks streamed to disk for the sieve; no full-size numpy stack (stories 4, 6).
5. `composite`: the two Composites built and written chunk by chunk.
6. `merge`: each Series raster warped to the Event grid on disk by GDAL, then OR, priority fill, Orbit Map and coverage done lazily per chunk (story 7).
7. Crash safety *within* each of the four rewritten stages (`event`, `flood-extent`, `composite`, `merge`): each, when it runs (forced or not), first deletes its own skip sentinel(s), partial files and scratch; rasters are written as `.partial` files renamed on success; `summary.json` is written atomically. A killed stage is therefore always redone by the next run. Cross-stage staleness (re-running an upstream stage leaves downstream sentinels in place) is today's behaviour and is not changed: after re-running a stage, run the later ones with `--force` (Failure modes). `stack`, `despeckle` and `export` keep today's file handling (follow-up below).

Published outputs: for a single-orbit Event (Saintes, the only published Event) every Layer stays bit-for-bit identical. For the merge (no multi-orbit Layer has ever been published), the global Layers are bit-equal to today's on grids small enough that GDAL warps in one piece; on larger grids GDAL's chunked warp (today: array to array; now: file to file) may place bilinear/nearest source samples differently, so values may differ from what the old code would give. Keeping today's whole-array warp would defeat the bounded-memory goal. The new merge is deterministic and independent of our chunk size.

Out (explicitly):

- **Zarr.** Decided with the project owner: deferred. eo-pipe writes and reads the Cube only as NetCDF (`XarrayOutput`, `_SpeckleFilterStep.execute` accepts `.nc`/`.tif`), and changing eo-pipe is outside this repo. The Cube stays `interim/<event>/cube.nc` and `filtered-cube.nc`. Bounded memory does not need Zarr: eo-pipe writes the NetCDF with on-disk chunks equal to its dask chunks (full time axis per spatial chunk; Saintes: `(31, 288, 288)`, uncompressed), so chunked reads are cheap; only read *parallelism* is lost (HDF5 global lock).
- `export` and `stack` logic (only the budget pass-through to eo-pipe changes).
- Detection rules (`docs/detection-rules.md`), Layers, Publish Contract, CLI flags, stage names: unchanged.
- A real Charente run. The Charente Series is not downloaded; this PR proves bounded memory with the synthetic memory gate below (sized so the old code fails it) and Saintes equality. A Charente run is a follow-up.
- Spatial tiling of the Area of Interest into independent pipelines, off-laptop compute.

## Surfaces touched

| File | Change |
|---|---|
| `src/flood_monitoring/config.py` | new `Processing` dataclass, `EventConfig.processing`, YAML `processing:` parsing |
| `src/flood_monitoring/paths.py` | new `EventPaths.merge_work` |
| `src/flood_monitoring/chunks.py` (new) | chunk planning, chunked opening of a Cube, dask + GDAL cache context |
| `src/flood_monitoring/raster.py` (new) | partial/commit writes, atomic JSON, file-based sieve, lazy mask opening; replaces `flood_extent.write_mask/read_mask/sieve_masks` |
| `src/flood_monitoring/aoi.py` | new `mask_like` (lazy Area of Interest mask) |
| `src/flood_monitoring/cube.py` | `check_cube` computes all variables in one lazy pass; new `georeferenced` |
| `src/flood_monitoring/stack.py`, `despeckle.py` | pass `memory_budget_mb` to eo-pipe; open the Cube chunked for `check_cube` |
| `src/flood_monitoring/detection.py` | kernels on `xr.DataArray`; `drop_share` replaced by `drop_counts`; new `series_drop_counts`, `to_db_lazy`, `flooded_areas`; `event_start` evaluates candidates in batches with early stop; `peak` takes counts |
| `src/flood_monitoring/event.py` | lazy rewrite of `run` |
| `src/flood_monitoring/flood_extent.py` | lazy rewrite of `run`; `load_vv_db`, `write_mask`, `read_mask`, `sieve_masks` removed; `extent_summary` takes counts |
| `src/flood_monitoring/composite.py` | `composite_bands` on `xr.DataArray`; lazy `run`; `write_composite` removed; `render_rgb` unchanged |
| `src/flood_monitoring/merge.py` | kernels on `xr.DataArray`; `coverage` / `rank_series` take counts / shares |
| `src/flood_monitoring/merge_stage.py` | warp to Event grid on disk, lazy merge, partial/commit writes; `reproject`, `load_composite`, `write_raster` replaced |
| `tests/…`, `tests/reference_inputs.py`, `tests/data/…`, `tests/manual/memory_check.py` | see Verification |
| `README.md` | new `## Configuration` section: `processing.memory_budget_mb`, chunk/cache behaviour, peak scratch disk |

Not touched: `cli.py`, `export.py`, `download.py`, `events/saintes-2026-02.yaml` (the new key is optional), `notebooks/results.ipynb` (uses `to_db` on numpy, `render_rgb`, `acquisition_dates`, all kept).

## Commit order on the feature branch

1. **Reference capture, on untouched code:** `tests/reference_inputs.py`, `tests/data/make_reference.py`, the generated `tests/data/reference/`, `tests/manual/memory_check.py`, and the gate's result on the old code (`tests/data/reference/memory-old.json`, which must show the old `event`, `flood-extent`, `composite` and `merge` over the threshold). `reference_inputs.config()` sets `processing` only if `EventConfig` has that field, so it runs on both old and new code.
2. **The rewrite** (Implementation plan order: config/paths, chunks/raster/aoi, kernels, stages, README), with the tests below.

## Interfaces

### Config (`config.py`)

```python
@dataclass(frozen=True)
class Processing:
    """Memory budget of every stage after download; sets the chunk size and GDAL cache (see chunks.py)."""
    memory_budget_mb: float = 4096.0

@dataclass(frozen=True)
class EventConfig:
    ...
    processing: Processing = Processing()
```

YAML (optional; absent or `processing: null` means defaults):

```yaml
processing:
  memory_budget_mb: 4096
```

`load_event_config` calls `_processing(raw.get("processing") or {}, path) -> Processing`, written like `_event_grid`: unknown keys raise `ValueError(f"{path}: unknown processing setting(s) {sorted(unknown)}")`; a value `float()` cannot convert raises `ValueError(f"{path}: processing memory_budget_mb must be a number")`; `float(value)` not `> 0` raises `ValueError(f"{path}: processing memory_budget_mb must be positive")`; `float(value)` not finite (`inf`, which `plan_chunks` would overflow on) raises `ValueError(f"{path}: processing memory_budget_mb must be finite")`.

### Paths (`paths.py`)

```python
@property
def merge_work(self) -> Path:
    """Scratch folder of the merge: every Series raster warped to the Event grid; removed after the merge."""
    return self.data_root / "processed" / self.event / "_merge"
```

### Chunks (`chunks.py`, new)

```python
from eo_pipe.io.memory import MIN_CHUNK_SIDE, OVERHEAD, ChunkPlan, plan_chunks

TILE = 256  # GeoTIFF block size of every raster we write

def reserved_mb(budget_mb: float, height: int, width: int) -> float:
    """Memory outside the chunks: 2 * gdal_cache_mb(budget_mb) (both GDAL libraries' caches) + height * width / 2**20
    (the whole-grid Area of Interest mask, 1 B/px, see aoi.mask_like)."""

def plan(budget_mb: float, bytes_per_pixel: float, height: int, width: int, stored: int = TILE) -> ChunkPlan:
    """left = budget_mb - reserved_mb(budget_mb, height, width) (callers always pass the full budget; eo-pipe applies
    its own x3 overhead). If left is below one minimal chunk (bytes_per_pixel * OVERHEAD * MIN_CHUNK_SIDE**2 / 2**20 MB),
    raise ValueError(f"memory_budget_mb={budget_mb:g} is too small: {reserved:.0f} MB go to the GDAL caches and the
    Area of Interest mask and one minimal chunk needs {minimal:.3f} MB; set memory_budget_mb to at least
    {math.ceil(budget_needed)}"), where budget_needed is the smallest budget whose `left` covers the minimal chunk
    (the cache grows with the budget, so this is solved, not just summed). Never lets eo-pipe raise its own
    message, which would quote the reduced budget.
    p = plan_chunks(left, bytes_per_pixel, height, width); the side is then aligned to *stored*, the side of the
    input's on-disk chunks: if p.side >= stored, rounded down to a multiple of stored; otherwise the largest divisor
    of stored that is <= p.side and >= MIN_CHUNK_SIDE (none: p.side unchanged). Every block then reads whole stored
    chunks (or one stored chunk) and xarray emits no "chunks separate the stored chunks" warning. Rounding only
    shrinks the side, so the budget still holds.
    print(f"chunks: {side} px, {p.workers} at a time"); return ChunkPlan(side, p.workers)."""

def open_cube(path: Path, budget_mb: float, bytes_per_date: float, time_chunk: int = -1) -> tuple[xr.Dataset, ChunkPlan]:
    """Open the (Filtered) Cube lazily, chunked {"time": time_chunk, "y": p.side, "x": p.side}.
    Sizes come from a first xr.open_dataset(path) used for metadata only (closed before returning); so does
    stored = ds["VV"].encoding["chunksizes"][-1] when the variable is chunked on disk with square y/x chunks
    (eo-pipe: 288 on Saintes), else TILE; passed to plan().
    bytes_per_pixel = bytes_per_date * (sizes["time"] if time_chunk == -1 else time_chunk).
    Calls plan() through the module global, so monkeypatching chunks.plan affects it."""

def gdal_cache_mb(budget_mb: float) -> int:
    """max(32, int(budget_mb // 16)): block cache of EACH of the two GDAL libraries (so both together take 1/8 of
    the budget); twice this is subtracted from the budget inside plan(). Also the warp_mem_limit of the merge's warp."""

@contextmanager
def scheduler(workers: int, budget_mb: float):
    """Two GDAL libraries are loaded: rasterio's bundled one and the system one used by osgeo (eo-pipe's SieveStep via
    osgeo_utils.gdal_sieve). Both caches are bounded:
        old = osgeo.gdal.GetCacheMax(); osgeo.gdal.SetCacheMax(cache_bytes)
        with dask.config.set(scheduler="threads", num_workers=workers), rasterio.Env(GDAL_CACHEMAX=cache_bytes): yield
        finally: osgeo.gdal.SetCacheMax(old)
    with cache_bytes = gdal_cache_mb(budget_mb) * 2**20."""
```

Rules for callers: stages import the module (`from . import chunks`) and call `chunks.open_cube` / `chunks.plan` / `chunks.scheduler`, never `from .chunks import plan`, so the test monkeypatch of `chunks.plan` reaches them. Each stage runs every compute, every GDAL step (`gdal_sieve`, `warp`) and every raster read inside `with chunks.scheduler(plan.workers, budget):`; kernel code calls plain `.compute()` / `dask.compute`. Every stage that plans chunks prints one `chunks:` line (stack and despeckle for `check_cube`, event, flood-extent, composite, merge).

What the budget covers: the chunks (planned), both GDAL caches and the Area of Interest mask (reserved in `plan`). Two GDAL working buffers are not reserved because they never run during a chunked compute: the merge's `warp` (bounded by `warp_mem_limit=gdal_cache_mb(budget)`, run before any lazy pass) and `gdal_sieve`'s region tables (run between two passes). Each therefore only coexists with the caches and the AOI mask, which leaves it the planned chunks' share of the budget.

`time` is always one chunk for the per-pixel-across-time steps (a median along a chunked axis is not supported by dask); the composite opens with `time_chunk=1` because it reads two time steps only.

Bytes per pixel declared by each stage (payload one chunk holds; eo-pipe multiplies by 3):

| Stage | `bytes_per_date` / `bytes_per_pixel` | why |
|---|---|---|
| `check_cube` (stack, despeckle) | `bytes_per_date=10` | VV and VH reduced in one `dask.compute`: float32 + bool mask each, per date |
| `event` | `bytes_per_date=40` | VV and dB (8 B); the baseline median on a `(k, s, s)` block: a compacted copy of the pixels with data plus `np.nanmedian`'s working copies (about 20 B); drop and validity temporaries (about 10 B) |
| `flood-extent` | `bytes_per_date=9` | VV, dB float32, water bool |
| `composite` | `bytes_per_date=28`, `time_chunk=1` | VV, VH, two dB copies, 3 output bands, float32 |
| `merge` | `bytes_per_pixel = 26 * n_series + 30` | per Series: extent, permanent (1 B), two 3-band Composites (12 B each); outputs |

These are estimates; the memory gate holds them to account (the budget is the contract, not the constants).

### Cube (`cube.py`)

```python
def check_cube(ds: xr.Dataset, nodata: float | None, expected: int | None = None) -> None
```

Same signature, rules and messages. The validity reduction `valid.any(dim=("y", "x"))` is built for every variable and all are computed in one `dask.compute(...)` (plain values on numpy-backed datasets), then the first failing (variable in `ds.data_vars` order, step in time order) raises as today.

```python
def georeferenced(ds: xr.Dataset, name: str) -> xr.DataArray:
    """Variable *name* with its CRS written from the spatial_ref coordinate (eo-pipe keeps CRS there)."""
    return ds[name].rio.write_crs(ds["spatial_ref"].attrs["spatial_ref"])
```

`stack.run`: `.add_step("stack_tiles", MergeBatch(), group=config.event, memory_budget_mb=config.processing.memory_budget_mb)`; after the move, `ds, plan = chunks.open_cube(paths.cube, budget, bytes_per_date=10)`, then `check_cube` inside `chunks.scheduler(plan.workers, budget)`, then `ds.close()`. `despeckle.run`: `memory_budget_mb=budget` on both `lee_filter` and `quegan_filter`; check as above on `paths.filtered_cube`; `expected` = `sizes["time"]` of a metadata-only `xr.open_dataset(paths.cube)`. File handling otherwise unchanged.

### Area of Interest (`aoi.py`)

```python
def mask_like(polygon: BaseGeometry, template: xr.DataArray) -> xr.DataArray:
    """Boolean (y, x) mask: True where the pixel centre is inside *polygon* (EPSG:4326).

    *template* is a georeferenced, dask-backed DataArray whose last two dims are (y, x); other dims are ignored.
    Output: dims ("y", "x"), the template's y/x coords, CRS and transform, chunks = the template's y/x chunks.
    The mask is aoi_mask(polygon, crs, template.rio.transform(), shape) computed ONCE on the whole grid (numpy,
    1 B/px, reserved from the budget by chunks.plan), then wrapped and .chunk()ed like the template. It is therefore
    today's mask bit for bit, whatever the chunking (rasterising per block with shifted transforms would compute
    pixel coordinates with different floating-point rounding, and a pixel centre on an edge could flip).
    template.rio.transform() returns a transform written with rio.write_transform exactly (merge: grid.transform);
    otherwise rioxarray derives it from the coords, as today's template_mask does."""
```

`aoi_mask` and `template_mask` stay. The whole-grid mask is the one array not bounded by the chunk plan: 153 MB on Charente, 16 MB on the memory gate's 4000 x 4000 grid.

### Raster I/O (`raster.py`, new)

```python
@dataclass(frozen=True)
class Raster:
    data: xr.DataArray      # (y, x) or (band, y, x); bool data is cast to dtype; numpy-backed data is .chunk()ed whole
    path: Path              # final path
    dtype: str              # "uint8" / "float32"
    crs: Any                # always given: Series CRS (from georeferenced) or grid.crs
    nodata: float | None = None
    long_name: str | None = None        # long_name attribute (Composites)
    transform: Affine | None = None     # exact transform to write (merge: grid.transform); None = from coords, as today

def partial(path: Path) -> Path:
    """path.with_name(path.name + ".partial"), e.g. flood-extent.tif -> flood-extent.tif.partial."""

def write_partial(rasters: list[Raster], *extra) -> tuple:
    """For each Raster: da = data.astype(dtype) (chunked if numpy-backed); da.attrs = {} and da.encoding = {} (attributes
    inherited from the Cube — e.g. VV.attrs["nodata"] = -32768 set by eo-pipe — must never reach a GeoTIFF: rioxarray
    would read it as nodata and fail on uint8, or write it as a tag); then rio.write_crs(crs), rio.write_nodata(nodata)
    when given, long_name attr when given, rio.write_transform(transform) when given; then
    da.rio.to_raster(partial(path), driver="GTiff", dtype=dtype, tiled=True, blockxsize=TILE, blockysize=TILE,
    lock=threading.Lock(), compute=False, recalc_transform=transform is None). Then ONE dask.compute(*writes, *extra),
    so the Cube is read once (checked: one read per Cube block for several writes plus extras). Returns the computed
    *extra* as dask.compute gives them back: numpy-backed DataArrays (0-d for sums), which callers turn into
    plain values with int() / bool() / iteration. Creates parent folders."""

def commit(paths: list[Path]) -> None:
    """os.replace(partial(p), p) for each p, in order."""

def clear(paths: list[Path], folders: list[Path] = ()) -> None:
    """Delete each p and partial(p) if present, and rmtree each folder (ignore missing)."""

def write_json(path: Path, obj: dict) -> None:
    """json.dumps(obj, indent=2) + "\\n" to partial(path), then os.replace — every summary.json write uses this."""

def sieve(masks: list[xr.DataArray], crs: Any, work: Path, size: int, connectedness: int,
          transform: Affine | None = None) -> list[Path]:
    """GDAL sieve of each boolean (y, x) mask, judged on the whole mask.

    rmtree(work); writes mask i as uint8 GeoTIFF work/raw/<i>.tif (write_partial + commit, one dask pass, tiled,
    Raster(..., crs=crs, transform=transform): the merge passes grid.transform, per-Series stages None as today);
    (work/"sieved").mkdir();
    SieveStep().execute(raw files, work/"sieved", threshold=size, connectedness=connectedness);
    rmtree(work/"raw") immediately; returns the sieved file paths in input order (StepOutput.outputs order).
    Sieved files: single-band uint8 0/1, no nodata, same CRS/geotransform (gdal_sieve copies them), striped."""

def open_mask(path: Path, like: xr.DataArray, side: int) -> xr.DataArray:
    """rioxarray.open_rasterio(path, chunks={"band": 1, "y": side, "x": side}), band squeezed, .astype(bool),
    y/x coords replaced by like's y/x (ValueError if shapes differ), CRS of like."""

def read_mask(path: Path) -> np.ndarray:
    """Boolean (y, x) numpy array of band 1 (tests and small rasters only)."""
```

Sieve approach (the issue asks to decide here): **GDAL sieve on one whole mask per date, file to file**. A region can cross any chunk edge and be bigger than any halo, so it must be judged on the whole mask; `gdal_sieve` reads and writes by scanlines (memory O(width + number of regions), covered by the memory gate's noisy mask), it is the same algorithm as today, so outputs stay identical, and it needs no new connected-component code. Same rule as the merge in #23: the merged Flood Extent is sieved whole too. Its output is striped (eo-pipe's `SieveStep` passes no creation options); reading `side`-wide windows from a striped uint8 file reads full-width rows. Cost on Charente (about 17 000 px wide, 153 MB per mask, side about 2048 at the default budget): about 8x read amplification, about 1.2 GB read per mask, about 13 GB for 11 masks, sequential reads from local disk; accepted for this PR (tiled sieve output is a follow-up). The sieve's region tables (O(width + number of regions)) are not governed by the budget; the memory gate measures them on a 4000 x 4000 mask with 2 % salt.

### Detection kernels (`detection.py`)

```python
def to_db(power: np.ndarray, nodata: float | None = None) -> np.ndarray
    # unchanged numpy kernel (the notebook uses it)

def to_db_lazy(power: xr.DataArray, nodata: float | None = None) -> xr.DataArray:
    """xr.apply_ufunc(to_db, power, kwargs={"nodata": nodata}, dask="parallelized", output_dtypes=[np.float32],
    keep_attrs=False), then .rio.write_crs(power.rio.crs) when power has a CRS. Variable attrs dropped, coords kept.
    The write_crs is required: rioxarray links a variable to its spatial_ref coordinate through
    encoding["grid_mapping"], which apply_ufunc drops, so without it rio.crs is None (checked) and the event
    stage's aoi.mask_like(polygon, db) would fail on every Event with an Area of Interest polygon."""

def drop_counts(db: np.ndarray, candidates: range, min_baseline: int, drop_db: float,
                inside: np.ndarray | None = None) -> np.ndarray:
    """(len(candidates), 2) int64 for a (T, y, x) block: per candidate k in *candidates* (a sub-range of
    min_baseline..T-1), column 0 the pixels
    with a valid Drop (np.isfinite(baseline - db[k]) & inside), column 1 those of them whose Drop > drop_db.
    baseline: NaN everywhere, then for pixels with at least one finite value in db[:k],
    np.nanmedian(db[:k][:, has], axis=0) — same values as today's np.nanmedian(db[:k], axis=0), without the
    all-NaN RuntimeWarning, so no warnings filter is needed (the old catch_warnings block is removed; it is not
    thread-safe under dask). Raises ValueError("need more than {min_baseline} Acquisitions to detect the Event
    Start, got {T}") if T <= min_baseline."""

CANDIDATE_BATCH = 8  # Event Start candidates per read of the Filtered Cube

def series_drop_counts(db: xr.DataArray, candidates: range, min_baseline: int, drop_db: float,
                       inside: xr.DataArray | None = None) -> Delayed:
    """Same length check, raised eagerly. Then delayed(drop_counts)(block, candidates, min_baseline, drop_db,
    inside_block) for each pair of db.data.to_delayed()[0, i, j] and inside.data.to_delayed()[i, j] (None when no
    inside), combined with delayed(sum)(parts). *inside* must have the same y/x chunks as db (mask_like guarantees it)."""

def event_start(db: xr.DataArray, min_baseline: int, drop_db: float, min_share: float,
                inside: xr.DataArray | None = None, batch: int = CANDIDATE_BATCH) -> tuple[int, list[float]]:
    """Early stop by batches, as today's loop stops at the first qualifying k: for each batch of *batch* candidates
    (min_baseline..T-1 in order), counts = series_drop_counts(db, batch_range, ...).compute() (one read of the Cube
    per batch); share = counts[:, 1] / counts[:, 0] (0.0 where counts[:, 0] == 0); the first k with share > min_share
    returns (k, [float(s) for every candidate up to and including k]). Shares of candidates after k in the same
    batch are computed but dropped, so the output equals today's. Raises
    ValueError("no Event Start: Drop share never exceeded {min_share:.1%} (max {max(shares):.2%})") as today.
    Cost: today's medians up to k plus at most batch - 1 extra, and ceil((k - min_baseline + 1) / batch) Cube reads;
    without batches every candidate up to T - 1 would be computed (about 52 medians on Charente instead of ~4)."""

def water(db: xr.DataArray, threshold_db: float) -> xr.DataArray            # db < threshold_db (NaN is not water), bool
def permanent_water(pre_water: xr.DataArray, fraction: float) -> xr.DataArray
    # pre_water.mean("time") > fraction, bool; ValueError("no pre-event Acquisitions to derive Permanent Water from") if sizes["time"] == 0
def flood_extent(water_masks: xr.DataArray, permanent: xr.DataArray) -> xr.DataArray   # water_masks.any("time") & ~permanent, bool
def flooded_areas(water_masks: xr.DataArray, permanent: xr.DataArray) -> xr.DataArray  # (water_masks & ~permanent).sum(("y", "x")), per time
def peak(areas: list[int]) -> int                                             # int(np.argmax(areas))
```

All mask kernels take and return **bool** DataArrays (never uint8: `~` on uint8 is bitwise). Share arithmetic is identical to today: today's share is `(drop[valid] > drop_db).mean()`, a float64 mean of 0/1 values, which is exactly `n_drop / n_valid` (both integers < 2**53). `event_start` reports shares up to k, as today's early-stopping loop did; the candidates of one batch share one read pass. `drop_share` is removed.

### Event stage (`event.py`)

```python
def run(config, paths, force=False):
    # skip / missing-input checks unchanged
    raster.clear([paths.summary])           # sentinel first: a killed forced run leaves no stale summary
    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=40)
    with ds, chunks.scheduler(plan.workers, budget):
        dates = acquisition_dates(ds["time"].values)
        db = to_db_lazy(georeferenced(ds, "VV"), ds.attrs.get("nodata"))
        inside = aoi.mask_like(config.aoi_polygon, db) if config.aoi_polygon is not None else None
        try:
            k, shares = event_start(db, rules.min_baseline, rules.drop_db, rules.event_share, inside)
        except ValueError as exc:            # both the too-short and the no-Event-Start errors get the orbit prefix
            orbit = f"relative orbit {paths.orbit}: " if paths.orbit is not None else ""
            raise ValueError(f"{orbit}{exc}") from exc
    # summary dict and print exactly as today; raster.write_json(paths.summary, summary)
```

### Flood-extent stage (`flood_extent.py`)

```python
def run(config, paths, force=False):
    # skip / missing-input checks unchanged
    raster.clear([paths.flood_extent, paths.permanent_water], [paths.water_masks, paths.sieve_work])  # sentinel first
    budget = config.processing.memory_budget_mb
    ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=9)
    with ds, chunks.scheduler(plan.workers, budget):
        dates = acquisition_dates(ds["time"].values); start = dates.index(summary["event_start"])
        post_dates = dates[start:]
        vv = georeferenced(ds, "VV"); crs = vv.rio.crs
        db = to_db_lazy(vv, ds.attrs.get("nodata"))
        wet = water(db, rules.water_db)
        template = wet.isel(time=0, drop=True)
        raw = [permanent_water(wet.isel(time=slice(None, start)), rules.permanent_fraction),
               *(wet.isel(time=i, drop=True) for i in range(start, len(dates)))]
        files = raster.sieve(raw, crs, paths.sieve_work, rules.sieve_size, rules.sieve_connectedness)  # pass 1: Cube read once
        sieved = [raster.open_mask(f, template, plan.side) for f in files]
        permanent = sieved[0]
        masks = xr.concat(sieved[1:], dim="time").assign_coords(time=ds["time"].values[start:])
        extent = flood_extent(masks, permanent)
        areas, n_permanent, n_extent = raster.write_partial(
            [raster.Raster(extent, paths.flood_extent, "uint8", crs)],
            flooded_areas(masks, permanent), permanent.sum(), extent.sum())                        # pass 2: masks only
    paths.water_masks.mkdir(parents=True)
    for day, f in zip(post_dates, files[1:]):
        os.replace(f, paths.water_mask(day))          # sieved files become the Water Masks (no rewrite)
    os.replace(files[0], paths.permanent_water)
    shutil.rmtree(paths.sieve_work)
    summary.update(extent_summary(rules, post_dates, [int(a) for a in areas], int(n_permanent), int(n_extent),
                                  pixel_area_km2(template)))
    raster.write_json(paths.summary, summary)        # summary before the sentinel
    raster.commit([paths.flood_extent])              # Flood Extent last: the stage's skip sentinel
    # print exactly as today
```

`extent_summary(rules, post_dates, areas: list[int], n_permanent: int, n_extent: int, pixel_km2: float) -> dict` returns the same keys, order and rounding as today (`peak = post_dates[peak(areas)]`). `pixel_area_km2(template)` unchanged (coords only).

Masks: single-band uint8 0/1, no nodata, CRS and transform of the Series. Water Masks and Permanent Water are `gdal_sieve`'s own files (pixel-identical to today's re-encoded copies; not published — export reads only the Flood Extent, Composites and Orbit Map — so file layout and tags may differ). The Flood Extent goes through `write_partial` (tiled 256, transform from coords as today).

### Composite (`composite.py`)

```python
def composite_bands(vv: xr.DataArray, vh: xr.DataArray, nodata: float | None = None) -> xr.DataArray:
    """(band, y, x) float32, band coord ["vv", "vh", "vv_minus_vh"]: xr.concat([vv_db, vh_db, vv_db - vh_db], "band")
    with to_db_lazy; .where(np.isfinite(out).all("band"), NODATA).astype("float32"). Lazy on dask input; same values
    as today."""
```

`run`: `raster.clear([paths.before_composite, paths.after_composite])` first (the skip test needs both, so both are the sentinel); `ds, plan = chunks.open_cube(paths.filtered_cube, budget, bytes_per_date=28, time_chunk=1)`; inside `chunks.scheduler(...)`, one `write_partial` with both `Raster(composite_bands(vv.isel(time=i, drop=True), vh.isel(time=i, drop=True), nodata), target, "float32", vv.rio.crs, NODATA, "VV dB, VH dB, VV minus VH dB")` (`vv`, `vh` from `georeferenced`); then `commit([before, after])`; prints as today. `render_rgb` unchanged.

### Merge kernels (`merge.py`)

```python
def valid_composite(composite: xr.DataArray) -> xr.DataArray            # (composite != NODATA).all("band") & np.isfinite(composite).all("band"), bool (y, x)
def coverage(counts: list[int], total: int) -> list[float]               # [c / total ...]; ValueError("the Area of Interest holds no pixel on the Event grid") if total == 0
def rank_series(shares: list[float]) -> list[int]                        # sorted(range(n), key=lambda i: (-shares[i], i))
def merge_flood_extent(extents: list[xr.DataArray], aoi: xr.DataArray) -> xr.DataArray     # functools.reduce(operator.or_, extents) & aoi, bool
def fill_composites(composites: list[xr.DataArray], order: list[int], aoi: xr.DataArray) -> tuple[xr.DataArray, xr.DataArray]
    # merged = xr.full_like(composites[0], NODATA, dtype="float32"); orbit_map = xr.zeros_like(aoi, dtype="uint8");
    # open_ = aoi; for rank, i in enumerate(order, start=1): take = open_ & valid_composite(composites[i]);
    #   merged = merged.where(~take, composites[i]); orbit_map = orbit_map.where(~take, np.uint8(rank)); open_ = open_ & ~take
    #   (DataArray.where keeps the caller's dims, so merged stays (band, y, x); xr.where would give (y, x, band),
    #   which rio.to_raster rejects)
    # return merged.astype("float32"), orbit_map.astype("uint8")   -- never an average (ADR 0001)
```

CRS and transform are not relied on through these kernels: the merge stage passes `crs=grid.crs, transform=grid.transform` on every `Raster`.

### Merge stage (`merge_stage.py`)

```python
def warp(path: Path, grid: Grid, resampling: Resampling, out: Path, budget_mb: float, nodata: float | None = None) -> None:
    """GDAL warp of every band of *path* onto *grid*, file to file: new GTiff *out* (grid width/height/crs/transform,
    src dtype and band count, nodata=*nodata*, tiled=True, blockxsize=blockysize=TILE), then
    rasterio.warp.reproject(rasterio.band(src, src.indexes), rasterio.band(dst, dst.indexes), src_nodata=nodata,
    dst_nodata=nodata, resampling=resampling, warp_mem_limit=chunks.gdal_cache_mb(budget_mb)). Uncovered pixels are
    *nodata* (or 0 when None), as today's reproject(). Tolerance as today (0.125). warp takes the budget as a
    `budget_mb` argument; the limit makes the warp's buffer part of the budget rather than GDAL's fixed default."""

def open_on_grid(path: Path, grid: Grid, side: int, mask: bool) -> xr.DataArray:
    """rioxarray.open_rasterio(path, chunks={"band": -1, "y": side, "x": side}, mask_and_scale=False), y/x coords
    replaced by grid.template(side)'s and CRS/transform written from the grid. mask=True: band squeezed,
    .astype(bool), dims (y, x). mask=False: (band, y, x) float32 Composite."""

class Grid:
    def template(self, side: int) -> xr.DataArray
        # dask zeros (y, x) uint8 chunked by side; coords = pixel centres from the transform (as today);
        # rio.write_crs(self.crs) and rio.write_transform(self.transform) (exact, for mask_like)
```

`run` flow (skip logic, missing-input errors, Main Series print, seam-check message, summary keys unchanged):

1. Missing-input checks as today. Then `raster.clear([paths.orbit_map, paths.flood_extent, paths.permanent_water, paths.before_composite, paths.after_composite], [paths.merge_work, paths.sieve_work])` (sentinels first).
2. `grid = event_grid(...)` as today. `plan = chunks.plan(budget, 26 * n + 30, grid.height, grid.width)`.
3. Everything below runs inside `chunks.scheduler(plan.workers, budget)`. For each Series orbit `o`: `warp` its `flood_extent` and `permanent_water` (nearest, nodata None) and both Composites (bilinear, `NODATA`) to `merge_work/orbit-<o>/{flood-extent,permanent-water,before-composite,after-composite}.tif`.
4. Open all with `open_on_grid` (which raises `ValueError` if a warped file's shape is not the grid's: their coords are always the grid's, so a coordinate join could never catch a mismatch); `inside = aoi.mask_like(polygon, grid.template(plan.side))` — the local is `inside`, never `aoi`: assigning `aoi` in `run` would make the `aoi` module a local name for the whole function and raise `UnboundLocalError` on this very call; Composites: `comp.where(valid_composite(comp), NODATA)` (today's `load_composite` rule); footprints `valid_composite(b) & valid_composite(a)`; `counts, total = dask.compute([(f & inside).sum() for f in footprints], inside.sum())`; `shares = coverage([int(c) for c in counts], int(total))`; `order = rank_series(shares)`.
5. `extent = raster.open_mask(raster.sieve([merge_flood_extent(extents, inside)], grid.crs, paths.sieve_work, ..., transform=grid.transform)[0], inside, plan.side) & inside`; `permanent = merge_flood_extent(permanents, inside)`; `before, orbit_map = fill_composites(befores, order, inside)`; `after, same_map = fill_composites(afters, order, inside)`.
6. `seam_differs, n_permanent, n_extent = raster.write_partial([...], (orbit_map != same_map).any(), permanent.sum(), extent.sum())`, every `Raster` with `crs=grid.crs, transform=grid.transform` and today's encodings: Flood Extent and Permanent Water `xr.where(inside, mask.astype("uint8"), np.uint8(255))` uint8 nodata 255 (with a bool mask and a Python 255, `xr.where` gives int64: 8 B/px per output inside each chunk, outside the merge's estimate — checked); Composites float32 nodata `NODATA`; Orbit Map uint8 nodata 0. If `bool(seam_differs)`: `raster.clear(those five paths)` and raise `RuntimeError("Before and After Composites would have different seams")`.
7. `shutil.rmtree` `merge_work` and `sieve_work`; commit Permanent Water and both Composites; write `aoi_polygon` (`write_text`, not a sentinel) and the summary with `raster.write_json` (areas from `int(n_permanent)`, `int(n_extent)`); commit Orbit Map, then Flood Extent last.

## UX flow

No CLI change. `uv run flood-monitoring run [event]` and `--stage <name>` behave as today. Users who need a smaller footprint add `processing: {memory_budget_mb: 2048}` to the Event YAML. eo-pipe logs its chunk plan as before; each stage that plans chunks prints `chunks: <side> px, <workers> at a time`.

Files: identical names and locations as today under `processed/<event>[/orbit-<n>]/`. Transient: `processed/.../_sieve/`, `processed/<event>/_merge/`, `*.tif.partial` and `summary.json.partial` while a stage writes.

Peak scratch disk (uncompressed): flood-extent `2 x (N_post + 1) x H x W` bytes during the sieve, then `(N_post + 1) x H x W` (those files become the Water Masks and Permanent Water). Charente estimate with 10 post-event dates: 3.4 GB peak. Merge: `26 x n_series x H x W` bytes, about 8 GB for two Series on Charente. Both go in the README `## Configuration` section.

## Raster semantics

- Cube and Filtered Cube: NetCDF, `(time, y, x)` float32 linear power, nodata in `ds.attrs["nodata"]` / `_FillValue` (Saintes: -32768) and NaN, CRS in the `spatial_ref` coordinate, native Series grid (UTM, 10 m). Unchanged.
- dB only after despeckle, through `to_db` / `to_db_lazy` (float32, invalid → NaN). Unchanged.
- Per-Series masks (Water Masks, Permanent Water, Flood Extent): uint8 0/1, no nodata, Series grid. Unchanged values.
- Composites: float32 `(band, y, x)`, VV dB, VH dB, VV minus VH dB, nodata `-9999`. Unchanged.
- Global layers on the Event grid (`event_grid` CRS, `pixel_size`, transform written exactly from `grid.transform`): Flood Extent and Permanent Water uint8 with 255 outside the Area of Interest; Composites float32 nodata -9999; Orbit Map uint8 rank, nodata 0. Encodings unchanged; values as stated under Scope "Published outputs" (bit-equal where GDAL warps in one piece; GDAL's chunked warp may place samples differently on large grids; independent of our chunk size).
- New: every GeoTIFF we write is tiled 256 x 256 (pixel values unchanged; export rewrites as COG anyway).
- Orbits: per-Series stages still run once per relative orbit and never mix Cubes; the merge is OR (masks) and priority fill (Composites), never an average (ADR 0001). Chunking is spatial only and never crosses Series.
- Chunk independence: per-pixel operations are local; global reductions are integer sums (exact); the sieve sees whole masks; `mask_like` rasterises by pixel centre per block with the exact block transform.

## Failure modes

| Situation | What the user sees |
|---|---|
| `memory_budget_mb` too small for the GDAL caches, the AOI mask and one 16 px chunk | `chunks.plan`'s `ValueError` "memory_budget_mb=<the budget as configured> is too small: … set memory_budget_mb to at least <a budget that works>", printed by the CLI as `error: …`, exit 1. (`stack` / `despeckle` pass the budget to eo-pipe, whose own message applies there.) |
| `processing` key invalid / unknown | `error: <path>: unknown processing setting(s) …` or `… must be positive` / `… must be finite`, exit 1 at config load |
| A time step with no valid pixel | `check_cube` `ValueError` as today, after stack or despeckle |
| Series too short / no Event Start | same `ValueError` messages as today, both with the orbit prefix |
| One of the four rewritten stages killed (forced or not) | its sentinel(s) were deleted before any other work, so the next run redoes it; leftover partials and scratch are deleted at that next start |
| An upstream stage re-run (e.g. `event --force` after a rule change) while downstream outputs exist | unchanged from today: downstream sentinels (`flood-extent.tif`, Composites) are still trusted, and a plain run skips them (`composite` may then report "has no Peak"). Re-run the later stages with `--force` (or `run --force`). |
| Killed while writing `summary.json` | only `summary.json.partial` exists; the old summary (or none) stays intact, never truncated |
| Stale Water Masks from a previous run with other dates | `water-masks/` deleted at stage start |
| Area of Interest outside the Event grid | `ValueError("the Area of Interest holds no pixel on the Event grid")` as today |
| A warped raster whose shape is not the Event grid's | `open_on_grid` / `open_mask` `ValueError` (shapes differ) instead of silent shrinkage |
| `flood-extent` fails and `--stage composite` is run alone | unchanged from today: `summary.json` still holds the previous run's Peak, so the Composites use it. The crash-safety guarantee covers rasters, not other stages' keys in the shared summary |
| Seams of Before and After differ | `RuntimeError` as today; no Layer committed |
| Disk full during a write | rasterio/GDAL error propagates as a traceback (`RasterioIOError` is an `OSError`, which the CLI does not catch — unchanged); no sentinel, so the next run redoes the stage |

## Verification criteria

### Reference inputs (`tests/reference_inputs.py`)

Importable from tests (pytest's default rootdir import mode puts `tests/` on `sys.path`; the two scripts insert `tests/` into `sys.path`). Deterministic: integer patterns, noise only from `np.random.default_rng(seed).random(...)`. Every fraction `f` of a size `n` becomes the pixel index `int(round(f * n))`; ranges are half-open `[a, b)`.

```python
EVENT = "synthetic-2026-01"
QUAD = ((2, 3), (45, 1), (46, 44), (1, 46))   # AOI corners, (col, row) on a 48-px grid; scaled by size / 48

def grid_polygon(size: int, x_shift_m: float = 0.0) -> Polygon
    # QUAD scaled to size (col * size / 48, row * size / 48, pixel corners), on the EPSG:32630 grid with origin
    # (500000 + x_shift_m, 5000000), 10 m, converted to EPSG:4326.

def config(*, dates: list[date], budget_mb: float | None = None, aoi_polygon=None, orbits=(8,),
           event_grid_crs=None, size: int = 48) -> EventConfig
    # No YAML, nothing written. project "flood-monitoring", event EVENT; window = Window(dates[0], dates[-1]);
    # series = Series("sentinel-1-rtc", dates[0], dates[-1], "descending", "IW", orbits, ("vv", "vh"));
    # area_of_interest = aoi_polygon.bounds if given, else the EPSG:4326 bounds of the size x size grid;
    # aoi_polygon as given; default Detection / Despeckle / CompositeRanges; event_grid = EventGrid(crs=event_grid_crs);
    # processing=Processing(budget_mb) only if budget_mb is not None and "processing" in EventConfig.__dataclass_fields__.

def paths(root: Path) -> EventPaths    # EventPaths(EVENT, data_root=root, repo_root=root / "repo")

def cube_dates(n: int) -> list[date]    # daily from 2026-01-01

def write_cube(path: Path, *, size: int, dates: int, chunk: int, flood_from: int, flood_fraction: float,
               salt_fraction: float, seed: int = 26) -> None
    # eo-pipe format: VV, VH float32 (time, y, x) linear power; EPSG:32630, origin (500000, 5000000), 10 m, pixel-centre
    # coords; spatial_ref via rio.write_crs(...).rio.write_coordinate_system(), grid_mapping popped from encoding and attrs
    # (as stack_tiles.py:303-306); ds.attrs["nodata"] = -32768 AND each variable attrs["nodata"] = -32768 and
    # encoding _FillValue = -32768 (as stack_tiles.py:303-306); encoding chunksizes (dates, chunk, chunk); written with
    # to_netcdf. Content, in this order:
    #   VV = 0.1 * (1 + 0.2 * (rng.random((dates, size, size)) - 0.5)) (about -10 dB); VH = VV * 0.25.
    #   Flood: dates >= flood_from, rows [0.25, 0.75), cols [0.25, 0.25 + flood_fraction): VV = VH = 0.005 (-23 dB).
    #     At size 48 with flood_fraction 0.5: rows/cols [12, 36), crossing both the 16 and 32 lines.
    #   River: rows [0.85, 0.875) (at least 1 row), all cols, all dates: 0.005 (Permanent Water).
    #   Salt: ONE draw, rng.random((size, size)) < salt_fraction, the SAME pixels in every date: 0.005. Being wet in every
    #     date they never Drop (no effect on the Event Start) and are raw Permanent Water removed by the sieve
    #     (isolated pixels, below sieve_size 20).
    #   NaN: column int(round(0.0625 * size)) (3 at size 48), all rows, all dates.
    #   Nodata: the square rows and cols [0.83, 0.92) ([40, 44) at size 48), all dates: -32768.

def single_series_config(root: Path, *, size: int = 48, dates: int = 20, budget_mb: float | None = None) -> tuple[EventConfig, EventPaths]
    # config(dates=cube_dates(dates), budget_mb=budget_mb, aoi_polygon=grid_polygon(size), size=size), paths(root). Writes nothing.

def single_series(root: Path, *, size=48, dates=20, chunk=16, flood_from=12, flood_fraction=0.5, salt_fraction=0.01,
                  budget_mb=None) -> tuple[EventConfig, EventPaths]
    # write_cube(paths.filtered_cube, ...) then single_series_config(...). Expected Event Start: date index flood_from
    # (the flood's share of the AOI is far above event_share 0.01; nothing else Drops).

def write_series(paths: EventPaths, *, size: int, x_shift_m: float, valid_cols: tuple[float, float],
                 flood_box: tuple[float, float, float, float], before: tuple[float, float], after: tuple[float, float],
                 summary: dict) -> None
    # Per-Series processed rasters written with rasterio (as tests/test_merge_stage.py:20-27 writes them), EPSG:32630,
    # origin (500000 + x_shift_m, 5000000), 10 m, size x size, no tiling:
    # before/after-composite.tif float32 3 bands (vv, vh, vv - vh) dB on cols valid_cols (all rows), NODATA elsewhere, nodata NODATA;
    # flood-extent.tif uint8 1 on flood_box (row0, row1, col0, col1), 0 elsewhere, no nodata;
    # permanent-water.tif uint8 1 on rows [0.85, 0.875), 0 elsewhere, no nodata; summary.json = summary.

def two_series_config(root: Path, *, size: int = 48, budget_mb: float | None = None) -> tuple[EventConfig, EventPaths]
    # config(dates=cube_dates(20), budget_mb=budget_mb, aoi_polygon=grid_polygon(size), orbits=(8, 110),
    #        event_grid_crs="EPSG:2154", size=size), paths(root). Writes nothing.

def two_series(root: Path, *, size: int = 48, budget_mb=None) -> tuple[EventConfig, EventPaths]
    # write_series for each orbit into paths.series_paths(o), then two_series_config(...):
    # orbit 8:   x_shift_m 0,  valid_cols (0, 2/3), flood_box (0.2, 0.4, 0.05, 0.25), before (-10, -16), after (-12, -18),
    #            summary {"event": EVENT, "event_start": "2026-01-13", "before": "2026-01-12", "peak": "2026-01-15",
    #                     "flooded_area_km2": {"2026-01-13": 0.1}, "flood_extent_km2": 0.1}
    # orbit 110: x_shift_m 35 (3.5 px), valid_cols (1/3, 1), flood_box (0.2, 0.4, 0.6, 0.8), before (-20, -26), after (-22, -28),
    #            summary {"event": EVENT, "event_start": "2026-01-14", "before": "2026-01-13", "peak": "2026-01-16",
    #                     "flooded_area_km2": {"2026-01-14": 0.1}, "flood_extent_km2": 0.1}
    # Valid footprints overlap on cols [1/3, 2/3) of size (a third).
```

`tests/data/make_reference.py` (commit 1, untouched code; `uv run python tests/data/make_reference.py`, in-process like pytest, which already runs in this environment): runs `event`, `flood-extent`, `composite` on `single_series(tmp)` and `merge` on `two_series(tmp)`, and saves to `tests/data/reference/{single,merge}/`: `arrays.npz` (one array per output raster, key = path relative to the processed folder, every Water Mask included), `meta.json` (per raster: `crs` WKT, `transform` 6 floats, `dtype`, `nodata`, `count`, `tags` = `src.tags()` dataset tags) and `summary.json`.

### pytest (synthetic only, no download)

- `tests/test_reference.py::test_single_series_matches_reference[16|7|1000]` and `::test_merge_matches_reference[16|1000]`: `chunks.plan` monkeypatched to return `ChunkPlan(side, 2)` and record calls; run the stages; `summary.json` equal; every raster `np.array_equal` to the reference (Composites bit-equal); CRS compared semantically (`CRS.from_wkt(a) == CRS.from_wkt(b)`; Water Masks and Permanent Water are now written by `gdal_sieve`, whose WKT text may differ), transform exactly, dtype, nodata and count equal; `tags` equal for the published rasters only (Flood Extent, both Composites, and in the merge the Orbit Map); the monkeypatch was called.
- `tests/test_merge_stage.py::test_large_merge_is_chunk_independent`: `two_series(size=1200)` (large enough that GDAL splits the warp; verified while implementing by checking that today's array warp and the file warp differ there, else the size is raised), merge run with `ChunkPlan(256, 2)` and `ChunkPlan(1024, 2)`: every global Layer bit-equal between the two runs; Orbit Map values only in {0, 1, 2}; Composites never a mix of two Series' values (each pixel equals one Series' warped value or NODATA).
- `tests/test_config_detection.py::test_processing_defaults`, `::test_processing_budget_is_read`, `::test_processing_null_means_defaults`, `::test_processing_rejects_non_positive_budget`, `::test_processing_rejects_non_numeric_budget`, `::test_processing_rejects_infinite_budget`, `::test_processing_rejects_unknown_key`.
- `tests/test_chunks.py::test_open_cube_keeps_time_in_one_chunk`, `::test_open_cube_spatial_chunks_follow_plan`, `::test_time_chunk_one_for_composite`, `::test_plan_subtracts_both_gdal_caches_and_aoi_mask_and_prints_chunk_line`, `::test_plan_aligns_side_to_stored_chunks` (stored 288: planned 700 → 576, planned 200 → 144, planned 17 → 16; stored 25 (no divisor in [16, side]) → side unchanged; stored 256 default for GeoTIFF inputs), `::test_open_cube_reads_stored_chunk_side_and_emits_no_chunk_warning` (Cube written with `chunksizes` (T, 24, 24), opened under `warnings.simplefilter("error")`), `::test_plan_too_small_names_configured_budget_and_a_sufficient_one` (budgets 1, 64, and just below the stated minimum raise quoting the configured value; the stated minimum itself plans without error), `::test_scheduler_bounds_both_gdal_caches_and_restores_osgeo` (`osgeo.gdal.GetCacheMax()` inside and after), `::test_stack_and_despeckle_pass_budget_to_eo_pipe` (monkeypatch `PipelineComposition.add_step`, capture kwargs of stack_tiles, lee_filter, quegan_filter), `::test_stages_plan_with_config_budget[event|flood-extent|composite|merge]` (record the `budget_mb` passed to `chunks.plan`).
- `tests/test_cube.py::test_chunked_cube_rejects_all_nan_step`, `::test_chunked_cube_rejects_all_nodata_step` (dataset `.chunk({"y": 2, "x": 2})`); existing tests kept.
- `tests/test_detection.py`: existing `event_start` tests rewritten on DataArrays with the same expected k and shares; `test_event_start_too_short_series` raises with the same message; `::test_event_start_same_result_for_any_batch` (batch 1, 3, 8, larger than T: same k and shares); `::test_event_start_stops_after_the_batch_holding_k` (`series_drop_counts` call count recorded); `::test_drop_counts_only_inside_the_area_of_interest` (replaces the `drop_share` test: shares 0.5 and 1/3); `::test_series_drop_counts_is_chunk_independent` (sides 3, 5, full; AOI cut by chunk edges); `::test_series_drop_counts_too_short_raises_before_compute`; `::test_drop_counts_emits_no_warning` (under `warnings.simplefilter("error")`, with all-NaN pixels); `::test_to_db_lazy_drops_variable_attrs_keeps_crs` (`rio.crs` equal to the input's); water / permanent / flood_extent / peak / flooded_areas on bool DataArrays with today's expected values.
- `tests/test_event.py::test_too_short_series_error_has_orbit_prefix`, `::test_killed_forced_run_removes_summary` (`series_drop_counts` monkeypatched to raise after an existing summary).
- `tests/test_aoi.py::test_mask_like_equals_aoi_mask_for_any_chunking` (sides 3, 7, full; including a polygon with an edge exactly on a column of pixel centres).
- `tests/test_merge_stage.py::test_merge_run_builds_the_aoi_mask` (guards the module-name shadowing: the merge runs end to end with an AOI polygon).
- `tests/test_raster.py::test_partial_name`, `::test_write_partial_then_commit`, `::test_write_partial_writes_exact_transform_and_crs`, `::test_write_partial_ignores_inherited_attrs` (a bool mask carrying `attrs["nodata"] = -32768` writes as uint8 with no nodata and no extra tags), `::test_write_partial_accepts_numpy_backed_data`, `::test_failed_compute_leaves_no_final_file`, `::test_write_json_is_atomic` (a failing `os.replace` leaves the old file intact), `::test_sieve_keeps_region_cut_by_chunk_edges_and_drops_small_ones`, `::test_sieve_removes_raw_masks`, `::test_open_mask_is_bool_on_like_coords`.
- `tests/test_flood_extent.py`: existing tests kept (they import `read_mask` from `raster`); `::test_run_leaves_no_partial_or_scratch_files`; `::test_leftovers_from_a_killed_run_are_removed` (pre-seed `flood-extent.tif.partial`, `_sieve/raw/0.tif`, `water-masks/2020-01-01.tif`; after the run, all gone and only the new dates exist); `::test_killed_forced_run_is_redone` (existing outputs, `--force`, `raster.write_partial` monkeypatched to raise; the sentinel is gone and a plain run recomputes).
- `tests/test_composite.py::test_killed_forced_run_removes_both_composites`; band tests on DataArrays with today's values; `render_rgb` tests unchanged.
- `tests/test_merge.py`: kernels on DataArrays with today's expected values; `fill_composites` output dims `(band, y, x)` and dtypes float32 / uint8; `coverage` / `rank_series` on counts / shares.
- `tests/test_merge_stage.py`: existing tests kept; `::test_merge_removes_scratch`; `::test_merge_leftovers_from_a_killed_run_are_removed` (pre-seed `_merge/orbit-8/x.tif`, `orbit-map.tif.partial`); `::test_killed_forced_merge_is_redone`.
- Whole suite: `uv run pytest`, all green.

### Manual, on real data (`saintes-2026-02`, single orbit; Filtered Cube on disk)

1. Commit-1 code: run `event`, `flood-extent`, `composite` into a temporary data root (`FLOOD_MONITORING_DATA_ROOT`, `interim/saintes-2026-02/filtered-cube.nc` symlinked to the real one) with `uv run flood-monitoring run --stage <s>` → baseline.
2. New code into another temporary root, same commands.
3. `summary.json` equal; every processed raster equal pixel for pixel, same CRS (semantic), transform, dtype, nodata; Flood Extent and Composites also same tags. Export is unchanged code reading these rasters, so equal processed rasters mean equal published Layers. The merge has no real multi-orbit data; the synthetic tests above cover it. The Event YAML used must keep its river-buffer polygon (`aoi_polygon` set): that is the path through `to_db_lazy` → `aoi.mask_like` whose CRS loss this run must rule out.

### Manual checks before merging (temporary data root each; `FLOOD_MONITORING_DATA_ROOT=<tmp>`, Cubes symlinked)

Not automated because they need real data, kill signals, root (tmpfs) or eyes. Record each outcome in the PR description.

1. **Whole pipeline, one process.** `/usr/bin/time -v uv run flood-monitoring run saintes-2026-02 --force` with `processing: {memory_budget_mb: 1024}`. Pass: "Maximum resident set size" ≤ the budget plus the memory gate's baseline; every processed raster and `summary.json` equal to the baseline of step 1 above. The memory gate runs one stage per process, so this is the only check of GDAL caches and dask settings carried from stage to stage by the CLI.
2. **`stack` and `despeckle` memory.** `/usr/bin/time -v … run saintes-2026-02 --stage stack --force`, then `--stage despeckle --force`, at budgets 1024 and 4096. The gate measures only `check_cube` for these stages, not eo-pipe's `stack_tiles` / `lee_filter` / `quegan_filter`, which run outside `chunks.scheduler` with GDAL's default cache (5 % of RAM, about 1.6 GB on the dev laptop). Record the peaks; a peak far over the budget becomes an eo-pipe issue, not a blocker here.
3. **Killed stage is redone.** For each of `event`, `flood-extent`, `composite` (Saintes) and `merge` (synthetic `two_series(size=1200)` data root): start `--stage <s> --force`, `kill -9` it once the `chunks:` line is printed, then check the stage's sentinel is absent, run `--stage <s>` without `--force`, and check it completes and that no `*.partial`, `_sieve/` or `_merge/` is left.
4. **Disk full.** `sudo mount -t tmpfs -o size=300m tmpfs /mnt/fm`, data root there with the Filtered Cube symlinked, run `--stage flood-extent`. Pass: an error (traceback accepted), no `flood-extent.tif`; after `sudo umount` and a roomy root, the rerun succeeds.
5. **Budget too small.** `memory_budget_mb: 64`, then `1`, then the minimum the error states. Pass: the first two print `error: memory_budget_mb=<64|1> is too small …` (the configured value, not the reduced one), exit 1; the stated minimum runs.
6. **Export end to end.** After check 1, `--stage export`. Pass: publish validation ok; each COG in `outputs/` pixel-identical to the baseline's (`rasterio` read + `numpy.array_equal`), same `rio info` profile apart from block layout.
7. **Merge, by eye.** Open the outputs of the synthetic `two_series(size=1200)` merge in QGIS. Pass: Orbit Map seam follows the valid footprints; no blended Composite values along the seam; flood patches crossing the orbit edge kept by the sieve.
8. **Warnings and wall time.** In check 1's log, count "separate the stored chunks" warnings (expected 0, chunk sides being aligned to the Cube's 288-px chunks) and compare per-stage wall time with the old code. A stage more than 2x slower is investigated before merging (most likely the striped sieve reads or HDF5 serialisation).

### Memory gate (hard gate before shipping)

`tests/manual/memory_check.py` (committed; not collected by pytest). Usage: `uv run python tests/manual/memory_check.py run <scratch dir> [--budget 256] [--rebuild] [--out <json>]`.

- Layout under `<scratch dir>`: `big-single/`, `big-merge/`, `base-single/`, `base-merge/`. Inputs are written only if missing (or with `--rebuild`):
  - `big-single`: `single_series(root, size=4000, dates=12, chunk=288, flood_from=9, flood_fraction=0.3, salt_fraction=0.02)` — 768 MB per variable, about 1.6 GB on disk; Event Start date index 9 (`min_baseline` 8).
  - `big-merge`: `two_series(root, size=4000)`.
  - `base-single` / `base-merge`: the same calls with `size=64, chunk=16` (all other arguments identical).
- Each measurement is one child process: `python tests/manual/memory_check.py child <stage> <root> <budget> <kind>` with `kind` in {single, merge}, started with `env=flood_monitoring._env.clean_environment(dict(os.environ))` (the CLI's clean GDAL/PROJ environment, since the script does not go through the CLI's re-exec). The child builds its inputs with `single_series_config` / `two_series_config` (no writing), runs the stage with `force=True`, and prints one JSON line `{"stage": ..., "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}`. Stages, each its own child: `check_cube` (`chunks.open_cube(filtered_cube, budget, bytes_per_date=10)` + `check_cube`, inside `chunks.scheduler`; on old code: `xr.open_dataset` + `check_cube` as today), `event`, `flood-extent`, `composite` on single; `merge` on merge.
- A child that exits non-zero or prints no JSON line (e.g. OOM-killed) is recorded with `max_rss_mb: null` and its return code, and counts as a failure.
- Baseline = max peak of the five children on the `base-*` inputs. Threshold = baseline + 1.5 x budget (budget 256 → baseline + 384 MB). GDAL caches (both libraries) are inside the measured RSS, bounded at `gdal_cache_mb(256)` = 32 MB each and reserved by `chunks.plan` together with the 16 MB AOI mask, so the chunks get 256 - 80 = 176 MB.
- Sizes are chosen so that the old code fails every stage while staying within a laptop's free memory: old `check_cube` loads a 768 MB variable plus masks; old `event` VV + dB + medians (about 2.5 GB); old `flood-extent` VV + dB + mask stacks (about 2 GB); old `composite` two 64 MB slices, dB copies, the 192 MB stack and its `where` copy (about 0.8 GB); old `merge` about 82 B/px x 16 Mpx = 1.3 GB.
- Output: a table (stage, peak, threshold, pass/fail) on stdout, and with `--out` a JSON file `{"budget_mb": ..., "baseline_mb": ..., "threshold_mb": ..., "stages": [{"stage": ..., "max_rss_mb": float|null, "returncode": int, "pass": bool}]}`; exit status 1 if any stage fails. Commit 1 runs it on the old code with `--out tests/data/reference/memory-old.json` (expected: every stage `pass: false`); the rewrite must exit 0. If it does not, the `bytes_per_*` constants are raised until it does.

## Out-of-scope follow-ups

- Zarr Cube: eo-pipe `XarrayOutput` writing `.zarr` by suffix and the speckle steps reading `.zarr`; then `EventPaths.cube` / `filtered_cube` renamed. To file as an eo-pipe issue.
- Crash safety for `stack` and `despeckle` (eo-pipe output moved into place before `check_cube`; a failed check leaves a Cube the skip trusts).
- A real Charente run (peak RSS, wall time, scratch disk vs the estimates above) once its Series is downloaded.
- Compress (DEFLATE) scratch masks and warped rasters; tiled `gdal_sieve` output (needs eo-pipe `SieveStep` creation options).
- A chunked connected-component labelling if `gdal_sieve`'s per-region memory ever dominates.
- "Memory budget" in `CONTEXT.md` (not tracked in this repo today).
