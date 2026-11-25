# Stage 5: composite

Makes two pictures to read the flood with the eye (`composite.py`): the **Before Composite** (the Acquisition just before the Event Start) and the **After Composite** (the Peak). Dates come from `summary.json`, so `flood-extent` must have run.

## What a Composite is

Three bands, each in dB, float32 in memory:

1. VV
2. VH
3. VV minus VH (the ratio)

A pixel invalid in either polarization is nodata in all three bands.

This needs both polarizations. With `assets: [vv]` the stage logs a warning (`no Composites for <event>`) and returns without writing; `export` then publishes the Flood Extent without imagery Layers.

## Storage

Files hold **int16 hundredths of a dB** (`round(dB / 0.01)`, nodata `-32768`), with the scale recorded in the GeoTIFF, DEFLATE-compressed. `encode` and `decode` convert between this and the in-memory dB float32.

## Rendering

`render_rgb` stretches each band between the `(low, high)` range of the `composite:` config section to 0-255 (red = VV, green = VH, blue = VV minus VH), nodata black. Defaults, chosen by eye on Saintes: VV `[-20, -3]`, VH `[-26, -9]`, VV−VH `[2, 12]`. The same ranges go into the publish manifest as the Layer's `rgb` hint. See `docs/composite-before-after.png`.

## Behaviour

- Written chunk by chunk (one time step at a time), as `*.partial` files renamed on success; `after-composite.tif` is committed last.
- The stage skips when both Composites exist.
- The VV band of each Composite is also written on its own (`extract_vv`), for the single-band VV Layers of the export; a skipped run still writes them if they are missing.
- Outputs: `processed/<event>/before-composite.tif`, `after-composite.tif`, `before-vv.tif`, `after-vv.tif`.
- `filtered-cube.nc` is deleted once the Composites exist, unless `--keep-intermediates`.
