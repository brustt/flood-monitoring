# Stages 3-4: event and flood-extent

The detection core. It reads only the **VV** band of the Filtered Cube, already in dB (stored as int16 hundredths of a dB, read as float32 with NaN for nodata, see [ingest](1-ingest.md)). The rules and the reasons behind each threshold are in [`../detection-rules.md`](../detection-rules.md); this page explains how the stages apply them. Kernels live in `detection.py`.

All thresholds come from the `detection:` section of the Event config.

| Key | Default | Used for |
|-----|---------|----------|
| `min_baseline` | 8 | earlier Acquisitions needed to estimate the Baseline |
| `drop_db` | 4.0 | a pixel is "changed" when its Drop exceeds this |
| `event_share` | 0.01 | share of changed pixels that marks the Event Start |
| `water_db` | -15.0 | VV below this is water |
| `permanent_fraction` | 0.5 | share of pre-event dates a pixel must be water on |
| `sieve_size`, `sieve_connectedness` | 20, 8 | removal of isolated water specks |

## 3. `event` (`event.py`): find the Event Start

For each candidate Acquisition *k*, starting at the `min_baseline`-th:

1. **Baseline** = per-pixel median of VV dB over all Acquisitions *before* k. Using only earlier dates keeps the flood out of the median.
2. **Drop** = Baseline − VV(k), in dB. A pixel is *changed* if Drop > `drop_db`.
3. **Share(k)** = changed pixels / pixels of the Area of Interest (the polygon's pixels, not its bounding box's).
4. **Event Start** = the first candidate whose share exceeds `event_share`. **Before** is the Acquisition just before it.

If no candidate qualifies the stage fails with `no Event Start: Drop share never exceeded …`; it never guesses. For a multi-orbit Event the error names the relative orbit.

Memory: each spatial chunk of the Cube (the whole time axis per chunk) returns partial counts, summed into one share per candidate. Candidates are evaluated in batches, one read of the Cube per batch, and evaluation stops after the batch holding the Event Start. With a polygon, the chunks outside `window.npz` hold no pixel of it and are skipped.

Output `processed/<event>/summary.json`:

```json
{
  "event": "saintes-2026-02",
  "event_start": "2026-02-05",
  "before": "2026-01-30",
  "acquisitions": 31,
  "rules": {"min_baseline": 8, "drop_db": 4.0, "event_share": 0.01},
  "drop_share": {"<date>": "<share per candidate up to the Event Start>"}
}
```

## 4. `flood-extent` (`flood_extent.py`): masks, Permanent Water, Water Footprint, Peak

Given the Event Start from `summary.json`, split the dates into pre-event and **Post-event Acquisitions** (at or after the Event Start).

1. **Raw water**: VV dB < `water_db` on every date. A fixed threshold, the same for every Acquisition (plain Otsu was tried and rejected, see `detection-rules.md`).
2. **Permanent Water (raw)**: pixels that are water on more than `permanent_fraction` of the **pre-event** dates.
3. **Sieve**: Permanent Water and every post-event Water Mask are sieved with eo-pipe's `sieve` (`sieve_size`, `sieve_connectedness`) to drop specks. Each mask is sieved as a whole, as files on disk.
4. **Water Masks**: the sieved post-event masks are kept as `water-masks/<yyyy-mm-dd>.tif`. A Water Mask includes rivers and lakes.
5. **Flood Extent** = union of the Water Masks of all Post-event Acquisitions, minus Permanent Water. Written as `flood-extent.tif` (uint8, 1 = flooded).
6. **Water Footprint** = union of the Water Masks of all Post-event Acquisitions, Permanent Water included. Written as `water-footprint.tif` (uint8, 1 = water): it is the Layer published as the result.
7. **Peak** = the Post-event Acquisition with the largest (Water Mask − Permanent Water) area.

The summary is extended with the Peak, the rules used and areas in km²:

```json
{
  "peak": "2026-02-23",
  "rules_extent": {"water_db": -15.0, "permanent_fraction": 0.5, "sieve_size": 20, "sieve_connectedness": 8},
  "flooded_area_km2": {"<post-event date>": "<km2>"},
  "permanent_water_km2": "<km2>",
  "flood_extent_km2": "<km2>"
}
```

`water-footprint.tif` then `flood-extent.tif` are committed last: the stage skips only when both exist, so a killed run is always redone.

Outputs under `processed/<event>/`: `flood-extent.tif`, `water-footprint.tif`, `permanent-water.tif`, `water-masks/`, updated `summary.json`.

## Reference result

On `saintes-2026-02` the rules give Event Start 2026-02-05, Before 2026-01-30, Peak 2026-02-23, Flood Extent about 3.5 % of the Area of Interest (≈ 7.8 km²).

## Failure modes

- Stage skipped although the config changed: re-run with `--force`; later stages too.
- `flood-extent` fails with `run the event stage first`: `summary.json` is missing.
- A budget too small for one 16 px chunk stops the stage with `memory_budget_mb=… is too small`.
