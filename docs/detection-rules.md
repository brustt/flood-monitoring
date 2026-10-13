# Detection rules (settled on saintes-2026-02, issue #5)

Input: the Filtered Cube, VV only, converted from linear power to dB (`10·log10`). The Filtered Cube has 31 Acquisitions (2025-09-02 … 2026-03-01), no nodata pixels. Parameters below are defaults for `events/<event>.yaml`; the reference Event is saintes-2026-02.

## Event Start

1. **Baseline** for candidate Acquisition *k*: per-pixel **median of VV dB over all Acquisitions before *k***. At least **8** earlier Acquisitions are needed, so candidates start at the 9th Acquisition (2025-10-20).
2. **Drop** = Baseline − VV(k), in dB. A pixel is *changed* if Drop **> 4 dB**.
3. **Share(k)** = changed pixels / all pixels of the Area of Interest.
4. **Event Start** = the **first** candidate with Share **> 1 %** (fixed fraction). **Before** = the Acquisition just before it.
5. If no candidate qualifies, the Event fails (no flood detected); do not guess.

Why: the Baseline from only earlier dates is causal (no flood in the median). At 4 dB the autumn noise is ≤ 0.1 % and the 2025-12-31 / 2026-01-06 wet episode (0.3–0.7 %, 3–5 % at 3 dB, so 3 dB is too low) stays below 1 %. 5 dB is too high (02-05 is 0.8–1.0 %). The margin on the 1 % line is real but thin (0.7 % vs 1.4–1.7 %); the largest-jump rule was not used because the first flood date is not the biggest step (02-17 and 02-23 jump more).

Result for Saintes: **Event Start 2026-02-05, Before 2026-01-30.** Share: 02-05 1.68 %, 02-11 1.43 %, 02-17 2.89 %, 02-23 3.41 %, 03-01 2.64 % (running Baseline, 4 dB).

## Water Mask (per Post-event Acquisition)

- **No Otsu.** The VV histogram of this Area of Interest is unimodal (mode ≈ −8 dB, ~96 % of pixels between −13 and −4 dB; the water tail is only a few %). Otsu cuts the land mode: 6–53 % "water" per date and thresholds from −7.8 to −12.6 dB, a different one each date. It is not usable here, pooled or per Acquisition.
- **Fixed threshold: VV < −15 dB** → water. Same value for every Acquisition. (Otsu restricted to pixels < −10 dB gives −14.3 dB, which agrees.) Sensitivity of the Peak area: −14 dB 3.8 %, −15 dB 3.3 %, −16 dB 2.9 %, −17 dB 2.6 %. The Peak date and the map shape do not change.
- Then **eo-pipe `sieve`** with `threshold=20`, `connectedness=8` (eo-pipe defaults, ~0.2 ha at 10 m). Checked with the real step on the Peak mask: 77 118 raw water px → 76 647 px. Sizes 50 and 100 start eating real flooded fields (75 709 / 74 057), 5 removes almost nothing.

## Permanent Water

Per-pixel share of the **Acquisitions before the Event Start** with VV < −15 dB (raw, before sieve); Permanent Water = share **> 0.5** (water in most pre-event dates), then `sieve` 20 / 8. It is 0.13 % of the Area of Interest (the narrow river bed and a few ponds). Using the Baseline instead was not tried; the "most dates" rule is sufficient and avoids a second threshold.

## Flood Extent

Union of the sieved Water Masks of all Post-event Acquisitions, minus Permanent Water.

## Peak

The Post-event Acquisition with the largest (Water Mask − Permanent Water) area.

Result for Saintes (−15 dB, sieve 20): flooded area 02-05 1.98 %, 02-11 1.81 %, 02-17 2.89 %, **02-23 3.30 %**, 03-01 2.78 % → **Peak 2026-02-23**. Flood Extent = 3.5 % of the Area of Interest (≈ 7.8 km²).

**Disagreement to check with the owner:** the issue expected a Peak around 2026-02-17. The rules give 2026-02-23 for every threshold (−14 … −17 dB) and sieve size (5 … 50) tried; 02-17 is consistently second. Acquisitions are 6 days apart, so the flood peak itself may fall near 02-17 with the water still large on 02-23; the pipeline's Peak is "largest area", as defined in `CONTEXT.md`. Maps for the owner: Before 01-30 shows only the river and a narrow dark band in the north; 02-05 to 03-01 show the Charente valley flooded, widest on 02-23.

## Prototype scripts

`prototypes/rules_explore*.py` and `prototypes/rules_sieve_eopipe.py` reproduce the numbers. They read the Filtered Cube from `$FLOOD_MONITORING_DATA_ROOT` layout and write scratch files to a job temp dir; they are throwaway. `rules_explore5.py` (area/Peak/Extent figures) uses a scipy stand-in for sieve because of that GDAL problem; `rules_sieve_eopipe.py` runs the real eo-pipe step on the Peak mask and confirms the effect is the same. The pipeline must call eo-pipe's `sieve`. `rules_sieve_eopipe.py` needs a GDAL environment without OTB's `LD_LIBRARY_PATH`/`PYTHONPATH` on this machine.
