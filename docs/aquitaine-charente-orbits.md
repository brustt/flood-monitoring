# Relative orbits for `aquitaine_charente_2026-02`

AOI: BD TOPO major hydro network (Nouvelle-Aquitaine + Pays de la Loire, 23,810 segments), buffered 5 km, dissolved,
simplified at 100 m (5,630 vertices): 53,925 km², bbox 390 x 565 km.
Built by `experiments/build_hydro_aoi.py`, orbits found by `experiments/s1_orbits_for_aoi.py`
(Planetary Computer `sentinel-1-rtc`, IW, 2025-09-01 to 2026-03-30).

| Relative orbit | State | AOI coverage | Acquisition dates | Slices/date | Selected |
|---|---|---|---|---|---|
| 8 | descending | 80.6% | 35 (2025-09-02 .. 2026-03-25, every 6 days) | 4.4 | yes, Main Series |
| 81 | descending | 51.4% | 36 (2025-09-01 .. 2026-03-30) | 4.4 | yes |
| 110 | descending | 26.6% | 34 (2025-09-03 .. 2026-03-26) | 2.5 | yes |
| 154 | descending | 3.9% | 34 | 1.5 | no, covered by 8/81/110 |
| 37 | descending | 0.2% | 33 | 1.0 | no |
| 30 | ascending | 78.4% | 35 | 4.5 | no |
| 132 | ascending | 58.5% | 35 | 3.9 | no |
| 103 | ascending | 14.1% | 35 | 3.9 | no |
| 59 | ascending | 5.6% | 35 | 2.0 | no |

Cumulative descending coverage: 8 alone 80.6%, 8 + 81 94.3%, 8 + 81 + 110 100%. Descending is enough, as in the
existing Events. Each orbit is acquired every 6 days; a few dates are missing for 110 and 154 (see the script output for the lists).

## Feasibility, Series 2025-01-01 to 2026-03-01

Orbit 8: 60 dates, 275 slices, grid 2.1 Gpx. Orbit 81: 62 dates, 275 slices, 1.35 Gpx. Orbit 110: 58 dates,
139 slices, 1.0 Gpx. Baselines measured on `charente-2026-02` (35 dates, 108 Mpx): raw Subsets and the Cube are
float32, uncompressed; Cube and Filtered Cube = 8 B x px x dates (31 GB measured).

| | Raw (vv+vh) | Cube | Filtered Cube |
|---|---|---|---|
| orbit 8 | 690 GB | 1,020 GB | 1,020 GB |
| orbit 81 | 470 GB | 670 GB | 670 GB |
| orbit 110 | 300 GB | 470 GB | 470 GB |
| total | 1.46 TB | 2.16 TB | 2.16 TB |

- Disk peak: about 5.8 TB (raw + Cube + Filtered Cube, the pipeline deletes none of them), plus about 1 TB of stage work
  folder for orbit 8 while it is built. Processed results: about 150-200 GB (scaled from 3.5 GB for Charente).
  The data disk has 288 GB free. With #33 (compressed Cubes) the Cube side shrinks, the raw side does not.
- RAM: bounded by `memory_budget_mb`, not by the data. Fixed part: 2 GDAL caches (2 GB at 16384) + the AOI mask of
  one Series (1 B/px, 2.0 GB for orbit 8) = about 4 GB; the rest goes to chunks, so a budget of 12-16 GB works and
  smaller chunks only cost time. The machine has 30 GB.
- Download: about 690 slices x 2 assets, 1.46 TB over the network.

## Feasibility, first estimate (Series 2025-09-01 to 2026-03-30)

- Slices to download: about 400 (8: ~154, 81: ~158, 110: ~85), x2 assets (vv, vh), about 800 files. Without #32 every
  Series is stacked over the whole bbox, not only the chunks touching the polygon.
- Grid: 390 x 565 km at 10 m is 2.2 Gpx per date. The whole-grid AOI mask (1 B/px) takes about 2.1 GB, so the default
  `memory_budget_mb: 4096` leaves 1.4 GB for chunks; the event sets 16384 (not yet tried on a real run).
- Uncompressed Cube: about 35 dates x 2 assets x 4 B x 2.2 Gpx = 600 GB per Series, 1.8 TB for three. This needs #33
  (compressed Cube) and #32 before a full run is realistic.

## int16 Cube and Filtered Cube, measured on `charente-2026-02` (#39)

One orbit, 35 dates, grid 15020 x 7207 px (108 Mpx), polygon of 13.3 Mpx, 4096 MB budget, stages run one after the other
from a copy of `raw/` (hard links), default settings (intermediates deleted).

| | float32, before | int16 dB, now |
|---|---|---|
| Filtered Cube | 30 GB (every chunk, uncompressed) | 1.32 GB (1083 of 6726 chunks of 64 px) |
| Cube | 30 GB (estimated, same as above) | 1.93 GB (346 of 1711 chunks of 256 px, 20.2%) |
| Peak disk of the run | about 94 GB (raw 34 + Cube 30 + Filtered Cube 30) | 41.7 GB, reached while stacking (raw 34 + float32 stack + Cube) |
| Disk after the run | 94 GB + 3.5 GB results | 3.5 GB results only |

Stage time and peak RSS: stack 201 s / 1.5 GB, despeckle 639 s / 2.8 GB, event 50 s / 0.7 GB, flood-extent 107 s / 1.9 GB,
composite 85 s / 6.1 GB.

Against the earlier float32 run of the same Event (which covered the whole bbox, so compared inside the polygon):

- Event Start 2026-01-06, Before 2025-12-31 and Peak 2026-02-23 are identical.
- Composites: same valid pixels, at most 1 stored unit (0.01 dB) apart.
- Flood Extent is not identical: 789,863 pixels against 791,460 (-0.2%), 1,919 pixels differ (0.24%). Permanent Water: 164 of
  149,208 differ. These are pixels within the rounding (0.005 dB) of the -15 dB threshold, and the sieve moves a few more.
- Outside the polygon the new run has no Flood Extent: the stages never read the chunks that were not stored.

The Cube stores the polygon's chunks plus the speckle filters' halo (346 chunks of 256 px); the Filtered Cube stores
only the chunks that touch the polygon (1083 chunks of 64 px), so the two counts differ. The float32 baseline sizes are
not like for like: it was uncompressed and unwindowed, the Cube figure is an estimate, and only one orbit was run, so the
peak disk of a 3-orbit run is not measured.

The float32 stack of the orbit being stacked is still on disk next to the Cube until the Cube is written; for orbit 8 of
`aquitaine_charente_2026-02` that is the peak (about the compressed float32 stack, 1.2-1.5x smaller than 1 TB, plus raw).
Writing the dB conversion inside eo-pipe's `stack_tiles` would remove it.

## Pipeline gaps for multi-orbit Events

Per-orbit loop in the CLI, merge stage, Orbit Map and Event summary already exist, and the config accepts
`relative_orbit: [..]`. Remaining:

1. `download.run` raises `NotImplementedError` for several orbits when called directly; only the CLI loop works around it (#23).
2. Whole-grid AOI mask and Cube size at 2.2 Gpx (see above): #32, #33.
3. No end-to-end run of a three-orbit Event yet, so the merge on a real overlap strip is untested (ADR 0001 fallback for false positives).
