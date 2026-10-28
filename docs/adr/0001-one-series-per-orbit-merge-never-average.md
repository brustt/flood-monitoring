# One Series per orbit; merge results, never average backscatter

An Area of Interest too large for one descending swath is processed as one **Series per relative orbit**, each with its own Cube, Baseline, Event Start, Water Masks, Permanent Water, Flood Extent and Composites. A **merge** stage then builds the global Layers on one Event grid.

- **Flood Extent** is the logical OR of the per-Series Flood Extents (water in at least one Post-event Acquisition), sieved after the merge.
- **Composites** take each pixel from one Series only: the **Main Series** (largest valid coverage of the Area of Interest, ties in config order) wherever it has data, the next Series only where it has none. An **Orbit Map** records the source.

## Why

Orbits pass on different days and see the overlap at different incidence angles, so backscatter from two orbits is not comparable and an average describes no real Acquisition. The Drop, Event Start and Peak are only meaningful against one orbit's own Baseline. Merging results (masks, pictures) rather than inputs (Cubes) keeps the detection rules unchanged.

## Consequences

- Event Start, Before and Peak are per Series; the global Layers mix dates, so every date is listed in the Event summary and in the Orbit Map legend.
- A single-orbit Event keeps the flat folders and skips the merge stage.
- The OR can add up false positives in overlaps; fall back to the priority rule for the Flood Extent if the overlap strip looks more flooded than its surroundings.
