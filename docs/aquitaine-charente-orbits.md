# Relative orbits for `aquitaine_charente_2026-02`

AOI: BD TOPO major hydro network (Nouvelle-Aquitaine + Pays de la Loire, 23,810 segments), buffered 5 km, dissolved,
simplified at 100 m (5,630 vertices): 53,925 km², bbox 390 x 565 km.
Built by `experiments/build_hydro_aoi.py`, orbits found by `experiments/s1_orbits_for_aoi.py`
(Planetary Computer `sentinel-1-rtc`, IW, 2026-01-01 to 2026-03-01, the Series of the Event YAML).

| Relative orbit | State | AOI coverage | Acquisition dates | Slices/date | Selected |
|---|---|---|---|---|---|
| 8 | descending | 80.6% | 10 (2026-01-06 .. 2026-03-01, every 6 days) | 4.4 | yes, Main Series |
| 81 | descending | 51.4% | 10 (2026-01-05 .. 2026-02-28) | 4.0 | yes |
| 110 | descending | 26.5% | 9 (2026-01-01 .. 2026-02-24, 2026-01-19 missing) | 2.6 | yes |
| 154 | descending | 3.9% | 10 | 1.5 | no, covered by 8/81/110 |
| 37 | descending | 0.2% | 9 | 1.0 | no |
| 30 | ascending | 78.4% | 10 | 4.4 | no |
| 132 | ascending | 58.5% | 10 | 4.0 | no |
| 103 | ascending | 14.1% | 10 | 3.8 | no |
| 59 | ascending | 5.6% | 10 | 2.0 | no |

Cumulative descending coverage: 8 alone 80.6%, 8 + 81 94.3%, 8 + 81 + 110 100%. Descending is enough, as in the
existing Events. Each orbit is acquired every 6 days; dates are missing for 110 and 37 (see the script output for the lists).

## Feasibility: disk, Series 2026-01-01 to 2026-03-01 (the Event YAML)

Estimated by `experiments/estimate_disk.py` from the STAC footprints and the AOI polygon, with the sizes measured on
`charente-2026-02` (raw Subsets 9.4 B per pixel of slice and bbox window; float32 stack 8 B per pixel and date of the 256 px
chunks touching the polygon; Cube 2.4 B and Filtered Cube 2.8 B, int16 zlib, #39). Checked against that run: estimated
31 GB raw and 39 GB peak against 34 GB and 41.7 GB measured. Raw Subsets are the whole bbox window of each slice: the
blocks outside the polygon are not skipped (a 2 km river buffer touches nearly every block anyway; not checked for this
fat polygon). Footprints include nodata corners, so raw is slightly high.

| | Dates | Slices | Raw (vv+vh) | float32 stack (temporary) | Cube | Filtered Cube |
|---|---|---|---|---|---|---|
| orbit 8 | 10 | 44 | 132 GB | 44 GB | 13 GB | 13 GB |
| orbit 81 | 10 | 40 | 77 GB | 28 GB | 8 GB | 8 GB |
| orbit 110 | 9 | 23 | 58 GB | 13 GB | 4 GB | 4 GB |
| total | | 107 | 267 GB | | 25 GB | 25 GB |

- Peak disk, orbit by orbit with intermediates deleted (#39): raw of all three orbits + the float32 stack and Cube of
  orbit 8 = about 324 GB. After the run: raw 267 GB + results. Results scale from 3.5 GB (Charente, 13 Mpx) to about 140 GB
  if linear in polygon pixels (539 Mpx); not measured, probably less.
- The data disk has 286 GB free: the run does not fit as it is. Options: delete each orbit's raw after its stack, or put
  `data/` on another disk. Raw is 80% of the total; the Cubes are small now.
- The disk scales with the number of dates. Series start 2025-12-01: 168 slices, 425 GB raw, 517 GB peak. Start
  2025-11-01: 227 slices, 572 GB raw, 692 GB peak. Start 2025-09-01: 346 slices, 873 GB raw, 1,050 GB peak.
- Download: 107 slices x 2 assets, 267 GB over the network.

**The Series start is too late for the detection.** Orbit 8 has 10 dates (first 2026-01-06) and `min_baseline` is 8, so the
earliest candidate Event Start is the 9th date, 2026-02-23. On `charente-2026-02` (orbit 8, Series from 2025-09) the Event
Start is 2026-01-06, with Before 2025-12-31, so this Series cannot find it and would report a later one. Orbit 110 has 9
dates (one candidate), orbit 81 has 10. To reach that Event Start, orbit 8 needs 8 dates before 2025-12-31, so the Series
must start on 2025-11-19 at the latest; 2025-11-01 (692 GB peak) leaves a margin. Not changed in the YAML: it was set to
2026-01-01 on purpose (24 h before this estimate), probably for disk.

## Feasibility: network, odc-stac `load` against raw Subsets (#42)

**Throughput** (`experiments/pc_throughput.py`, 2026-11-25, home link): 422 COG blocks of 512 px (vv and vh of 16 orbit 8
slices, inside the footprint and touching the AOI, 359 MB, 851 kB per block, from 833 to 866 kB), one COG open per block
as odc-stac does per chunk read, each concurrency in a fresh process. MB/s counts the block bytes only. GDAL settings of
`prototypes/odc_stac_cube.py` plus `GDAL_HTTP_MULTIPLEX=YES` and `GDAL_HTTP_VERSION=2`.

| Concurrent reads | 1 | 4 | 8 | 16 | 32 | 1 again | curl, 1 stream, 200 MB |
|---|---|---|---|---|---|---|---|
| MB/s | 2.78 | 4.76 | 5.05 | 5.00 | 5.24 | 2.81 | 5.51, then 5.37 |

- The link is the limit, about 5 MB/s (40 Mbit/s): one plain stream already reaches it, and 8 or more parallel reads
  reach the same. A first, smaller run (58 blocks) gave the same figures.
- One read at a time gets half of it: each block is a COG open plus a range request, so the latency is not hidden.
  The 1.4 MB/s of the prototype on Saintes was not the link: `write_db_cube` reads a chunk's dates one after another.
- Azure Blob answers in HTTP/1.1 only (curl negotiates 1.1, prior-knowledge HTTP/2 fails), so the multiplexing
  settings do nothing; the parallelism has to come from threads.
- The second level-1 run matches the first, so the server does not cache the blocks between runs.

**Bytes to fetch** (`experiments/estimate_disk.py`): COG blocks of each slice, counted on its 512 px block grid, data
blocks only (touching the footprint). Raw Subsets read the blocks overlapping the AOI bbox. odc-stac reads, per 512 px
chunk of `aoi.chunk_window` (margin 6 px, Lee + Quegan), the blocks that the chunk overlaps, on the grid of the polygon
bounds in the orbit's main UTM zone (32631 for 8 and 110, 32630 for 81). The window holds 3,080 (3,095 for 81) of 9,462 chunks (33%).
GB = blocks x 2 assets x 851 kB.

| Series start | Orbit | Slices | Raw Subsets: blocks, GB | odc-stac unique blocks, GB | odc-stac fetches, GB |
|---|---|---|---|---|---|
| 2026-01-01 | 8 | 44 | 59,149, 101 GB | 31,260, 53 GB | 101,012, 172 GB |
| | 81 | 40 | 37,280, 63 GB | 15,960, 27 GB | 51,787, 88 GB |
| | 110 | 23 | 25,554, 43 GB | 9,946, 17 GB | 31,995, 54 GB |
| | total | 107 | 207 GB | 97 GB | 314 GB |
| 2025-11-01 | 8 | 94 | 126,145, 215 GB | 66,617, 113 GB | 215,880, 367 GB |
| | 81 | 85 | 81,434, 139 GB | 35,799, 61 GB | 116,608, 198 GB |
| | 110 | 48 | 53,446, 91 GB | 20,997, 36 GB | 67,539, 115 GB |
| | total | 227 | 445 GB | 210 GB | 680 GB |

- The window halves the blocks: odc-stac needs 47% of the blocks of the raw Subsets (39% to 53% by orbit).
- But the slice grids are not aligned with the chunks (each frame has its own origin, and 10 of the 44 orbit 8 slices
  are in zone 30), so a 512 px chunk overlaps about 4 blocks and each block is read by about 3.2 chunks. Each chunk read
  opens the COG anew, and GDAL's curl cache (16 MB per process, `CPL_VSIL_CURL_CACHE_SIZE`) holds about 19 blocks while one
  chunk reads about 80 (10 dates x 2 assets x 4), so the "fetches" column, without any cache hit, is what the prototype
  would download as it is. Not measured on a real run.
- Raw Subsets on the network (207 GB) are less than on disk (267 GB, 9.4 B/px): the COGs are 6.5 B/px for vv + vh.
- The block size is that of full data blocks; edge blocks with nodata are smaller, so all GB figures are a little high.

**Estimated `load` wall time** at 5.0 MB/s (bytes / throughput, 8 or more parallel reads):

| Series start | Orbit | Raw Subsets download | odc-stac, no refetch | odc-stac, as it is |
|---|---|---|---|---|
| 2026-01-01 | 8 / 81 / 110 | 5.6 / 3.5 / 2.4 h | 2.9 / 1.5 / 0.9 h | 9.6 / 4.9 / 3.0 h |
| | Event | 11.5 h | 5.4 h | 17 h |
| 2025-11-01 | 8 / 81 / 110 | 12 / 7.7 / 5.1 h | 6.3 / 3.4 / 2.0 h | 20 / 11 / 6.4 h |
| | Event | 25 h | 12 h | 38 h |

**Conclusion: hours per orbit, not days, as long as each block is fetched once** (12 h for the whole Event with the
2025-11-01 start `min_baseline` needs). Without that, odc-stac downloads more than the raw Subsets (38 h). The ways to
fetch each block once (chunks larger than 512 px, a larger curl cache with chunks read in order, or caching the blocks)
are not tested. At this link the parallelism only needs to be about 8 reads; more does not help.

## Feasibility: RAM

Bounded by `memory_budget_mb`, not by the data. Fixed part: 2 GDAL caches (2 GB at 16384) + the AOI mask of one Series
(1 B/px, 2.0 GB for orbit 8: grid 2.1 Gpx; 81: 1.35 Gpx; 110: 1.0 Gpx) = about 4 GB; the rest goes to chunks, so a budget
of 12-16 GB works and smaller chunks only cost time. The machine has 30 GB.

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
