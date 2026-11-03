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

## Feasibility

- Slices to download: about 400 (8: ~154, 81: ~158, 110: ~85), x2 assets (vv, vh), about 800 files. Without #32 every
  Series is stacked over the whole bbox, not only the chunks touching the polygon.
- Grid: 390 x 565 km at 10 m is 2.2 Gpx per date. The whole-grid AOI mask (1 B/px) takes about 2.1 GB, so the default
  `memory_budget_mb: 4096` leaves 1.4 GB for chunks; the event sets 16384 (not yet tried on a real run).
- Uncompressed Cube: about 35 dates x 2 assets x 4 B x 2.2 Gpx = 600 GB per Series, 1.8 TB for three. This needs #33
  (compressed Cube) and #32 before a full run is realistic.

## Pipeline gaps for multi-orbit Events

Per-orbit loop in the CLI, merge stage, Orbit Map and Event summary already exist, and the config accepts
`relative_orbit: [..]`. Remaining:

1. `download.run` raises `NotImplementedError` for several orbits when called directly; only the CLI loop works around it (#23).
2. Whole-grid AOI mask and Cube size at 2.2 Gpx (see above): #32, #33.
3. No end-to-end run of a three-orbit Event yet, so the merge on a real overlap strip is untested (ADR 0001 fallback for false positives).
