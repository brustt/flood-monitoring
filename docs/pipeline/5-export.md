# Stage 7: export

Turns the processed rasters into the **Layers** of the Event's **Published Result** (`export.py`). It always rewrites its outputs: they are small and must match the processed rasters.

## Layers

| Slug | Source | Role | Shown | Notes |
|------|--------|------|-------|-------|
| `water-footprint` | `water-footprint.tif` | `result` | yes | categories 0 "No water" (transparent), 1 "Water" (`#1f78b4`); nodata 255 |
| `s1-before-composite` | `before-composite.tif` | `imagery` | no | `acquired` = Before date, `rgb` bands 1-3 with the `composite:` ranges |
| `s1-after-composite` | `after-composite.tif` | `imagery` | no | `acquired` = Peak date, same ranges |
| `s1-before` | `before-vv.tif` | `imagery` | no | VV only, `greys_r` colormap over the `composite.vv` range |
| `s1-after` | `after-vv.tif` | `imagery` | no | VV only, same colormap and range |
| `orbit-map` | `orbit-map.tif` | `result` | no | multi-orbit only; one category per orbit with its dates |

The Flood Extent (`flood-extent.tif`) stays in `processed/` with its area in `summary.json`, but is not a published Layer. Display ranges are written in stored units (hundredths of a dB, e.g. `[-2000, -300]` for -20 to -3 dB): the tile server reads the int16 values as they are. Both sides of each before/after pair get the same range, so a colour difference is in the data, not in the stretch.

`s1-before-composite` and `s1-after-composite` are compared in the Platform (`compare`). A VV-only Series has no Composites: the four imagery Layers and the comparison are left out, with a warning. Having only one of the two Composites is a broken run and fails.

## Steps

1. Check that the inputs exist (`run the <stage> stage first` otherwise) and that `summary.json` has `before` and `peak`.
2. Write each Layer as a **Cloud-Optimized GeoTIFF** in `outputs/<event>/`: 256 px tiles, DEFLATE with predictor, internal overviews (nearest for categorical Layers, average for Composites), in the raster's own CRS.
3. Write `publish/<event>.publish.yaml`: title (`saintes-2026-02` → "Saintes flood, February 2026"), `start`/`end` from the config `window`, Layers, attribution "Contains modified Copernicus Sentinel data <year>".
4. Validate the manifest against `publish/project.yaml` with `platform_publish.validate`. Failure raises with the list of errors.
5. Archive: copy `outputs/<event>/` to `<data root>/outputs/<yyyymmddThhmmss>/<event>/`.

`publish/project.yaml` (Project slug, title, licence, attribution) is written once by hand.

## Not done here

Publishing to the Platform itself. The stage prepares and validates the manifest and the COGs; uploading is outside this repository.
