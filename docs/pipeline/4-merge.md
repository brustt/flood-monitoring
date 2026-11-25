# Stage 6: merge (multi-orbit Events)

An Area of Interest too large for one descending swath is processed as **one Series per relative orbit** (`relative_orbit: [8, 15]` in the config). Stages 1-5 run for each orbit in its own `orbit-<n>/` folders, one orbit after the other,; `merge` then builds the global Layers on one **Event grid**. Rationale: [ADR 0001](../adr/0001-one-series-per-orbit-merge-never-average.md).

A single-orbit Event prints `skip: a single Series already gives the global Layers` and does nothing. (`load` builds each orbit's Cube in turn; `download` alone does not support several orbits, see issue #23.)

## The rule: merge results, never backscatter

Orbits pass on different days and see the overlap at different incidence angles, so backscatter from two orbits is not comparable. Event Start, Baseline, Drop and Peak are meaningful only against one orbit's own data. So detection runs per orbit, unchanged, and only the *results* are merged.

## Steps (`merge_stage.py` for I/O, `merge.py` for the pure kernels)

1. **Event grid**: a projected grid covering the Area of Interest, snapped to multiples of `event_grid.pixel_size`, in `event_grid.crs` (or the CRS of the first Series).
2. **Warp** each Series' Flood Extent, Water Footprint, Permanent Water and two Composites onto that grid with GDAL, file to file (nearest for masks, bilinear for Composites), into `processed/<event>/_merge/`, removed afterwards.
3. **Coverage and ranking**: the share of the Area of Interest each Series' Composites cover. The **Main Series** has the largest coverage; ties follow config order.
4. **Flood Extent** = logical OR of the per-Series Flood Extents, kept inside the Area of Interest, then sieved *after* the merge so patches cut at an orbit edge are judged whole.
5. **Water Footprint** = OR of the per-Series Water Footprints, kept inside the Area of Interest; not sieved again (each is already a union of sieved Water Masks). **Permanent Water** = OR of the per-Series Permanent Water.
6. **Composites**: priority fill. Each pixel is taken from the first Series in rank order that has data there, never averaged. The **Orbit Map** records the rank of the source (1 = Main Series, 0 = nodata).
7. **Consistency check**: the Before and After Composites must have the same seams, otherwise the stage fails and clears its outputs.

Each Layer is written in its own pass, as `*.partial` files committed at the end. The Flood Extent and the Orbit Map are the skip sentinel.

## Outputs (`processed/<event>/`)

`flood-extent.tif`, `water-footprint.tif`, `permanent-water.tif`, `before-composite.tif`, `after-composite.tif` (with `before-vv.tif`, `after-vv.tif`), `orbit-map.tif`, `area-of-interest.geojson`, and `summary.json`:

- `main_series`, plus `event_start`, `before` and `peak` of the Main Series;
- a `series` list, in rank order, with each orbit's `coverage_share`, own dates, areas;
- global `flood_extent_km2` and `permanent_water_km2`.

The global Layers mix dates, which is why every orbit's dates are listed in the summary and in the Orbit Map legend that `export` publishes.

## Known trade-off

The OR can add false positives in an overlap. If the overlap strip looks more flooded than its surroundings, fall back to the priority rule for the Flood Extent (ADR 0001).
