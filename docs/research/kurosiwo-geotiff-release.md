# KuroSiwo GeoTIFF release: layout, units, labels, splits

Research for #48 (part of map #45). Question: what does the KuroSiwo GeoTIFF release contain, in enough detail to specify a converter to the canonical patch layout?

Checked on 2026-11-26. Sources, cited below by tag:

- **[HF-GT]** `orion-ai-lab/Kuro-Siwo-GeoTIFFs` on Hugging Face: dataset card and `api/datasets/.../tree/main` listing, revision `2b14973`.
- **[HF-WDS]** `orion-ai-lab/Kuro-Siwo-Webdataset`: card and tree, revision `e8e61b7`.
- **[GH]** `github.com/Orion-AI-Lab/KuroSiwo` at commit `4347ed1`: `README.md`, `dataset/Dataset.py`, `catalogue/catalogue.py`, `catalogue/catalogue.yaml`, `configs/train/data_config.json`, `configs/grd_preprocessing.xml`, `utilities/utilities.py`, `training/change_detection_trainer.py`, and `pickle/*.gz`.
- **[PAPER]** Bountos et al., *Kuro Siwo*, NeurIPS 2024 Datasets & Benchmarks, arXiv:2311.12056 (latest version, 2024-11-01), main text and supplement.
- **[SAMPLE]** The first 8 MiB of `GRD/00000.tar` from [HF-GT], fetched with an HTTP range request. Two grids from it were inspected with rasterio: one labelled grid (`1111009/01/0002e0ce…`) and one unlabelled grid (`1111009/00/92/92dcb5bc…`).
- **[PKL]** The two grid pickles from [GH], read with a restricted unpickler (no class loading) and summarised.
- **[WDS-HDR]** The tar headers and `info.json` of the first samples in `train_GRD/shard-00000.tar` and `test_GRD/shard-00000.tar` from [HF-WDS], read with 512-byte range requests.
- **[PC]** The Planetary Computer STAC collection `sentinel-1-rtc` (description and item assets).

## 1. Distribution, size, licence

- **Packaging: plain uncompressed tar shards.** The GeoTIFF repo has no loose GeoTIFFs. It has `GRD/00000.tar` … `GRD/00034.tar` (35 shards, about 20 GB each, **699.15 GB** in total) and `SLC/00000.tar` … `SLC/00015.tar` (16 shards, **316.34 GB**). [HF-GT tree] `tar -t` reads the raw bytes with no decompression, so the shards are uncompressed. [SAMPLE]
- The paper gives about 705.8 GB for the GRD component (with DEMs and metadata) and about 492.6 GB for SLC. [PAPER suppl. §1]
- **Labelled and unlabelled grids share the same shards.** `GRD/00000.tar` opens with unlabelled grids of activation 1111009 (`data/1111009/00/…`), followed by labelled grids (`data/1111009/01/…`). [SAMPLE] No index of shard offsets is published. **To get the labelled grids out of the GeoTIFF release, every GRD shard (about 700 GB) has to be streamed and filtered by path.**
- **The Hugging Face dataset card does not describe the GeoTIFF repo.** Its "Sample fields" section (`flood_vv.npy`, `mask.npy` …) is copied from the webdataset card. The YAML declares configs `GRD: GRD/*.tar` and `SLC: SLC/*.tar`. [HF-GT card]
  - Inference: inside these tars every file has its own basename, so a webdataset loader (and therefore `datasets.load_dataset`) would treat each file as a separate sample. Read the shards with `huggingface_hub` (`hf_hub_download`, or an HTTP stream of `resolve/main/GRD/NNNNN.tar`) plus `tarfile` in stream mode.
- **Sample counts:**
  - Labelled: **67,490** time series (202,470 SAR images) of 224×224 tiles over **43** flood events. [PAPER §3, Tab. 1] The test pickle has exactly 67,490 grids over 43 activations and 72 AOIs. [PKL]
  - Unlabelled: **466,357** time series (Tab. 1). The text gives 533,847 time series and 1,601,511 images, which equals labelled plus unlabelled (533,847 − 67,490 = 466,357). [PAPER §3]
- **Licence: the sources disagree.** The Hugging Face cards (both repos) and the GitHub README say **CC BY 4.0**. [HF-GT, HF-WDS, GH README] The paper says MIT (§3 footnote, suppl. §9). The GitHub repository's code licence is MIT. [GH]
- Older mirrors: Dropbox tarballs (`download_kuro_siwo.sh` fetches `catalogue.gpkg` plus `00.tar.gz` … `10.tar.gz`), which extract to the same `KuroSiwo/data/...` tree. [GH README, download script]

## 2. Folder hierarchy and per-grid files (GRD)

The path is built by `CatalogueGrid.path` in [GH `catalogue/catalogue.py`] and confirmed in [SAMPLE]:

```
data/<actid>/<aoi:02d>/<grid_hex>/                  # labelled grid (inside an annotated AOI)
data/<actid>/00/<grid_hex[0:2]>/<grid_hex>/         # unlabelled grid (outside any AOI)
```

- `actid` is the activation (event) id, for example `1111009` (Pakistan, Larkana).
- `grid_hex` is the grid UUID without dashes. `info.json` holds the same UUID with dashes as `grid_id`.
- The pickle keys and their `path` values use the form `<actid>/<aoi>/<hex>`, relative to `data/`. [PKL]

Files in one labelled grid, with the name pattern `<ptype><rank>_<pname>_<actid>_<aoi>_<YYYYMMDD>.tif`:

| File | Content | dtype | nodata (tag and `info.json`) |
|---|---|---|---|
| `MS1_IVV_…` / `MS1_IVH_…` | post-event ("master") VV / VH | float32 | 0.0 |
| `SL1_IVV_…` / `SL1_IVH_…` | first pre-event ("slave", rank 1) VV / VH | float32 | 0.0 |
| `SL2_IVV_…` / `SL2_IVH_…` | second pre-event (rank 2) VV / VH | float32 | 0.0 |
| `MK0_MLU_…` | label | uint8 | 3 |
| `MK0_MNA_…` | valid-pixel mask | uint8 | 0 |
| `MK0_DEM_…` | SRTM 1 Sec elevation (m) | float32 | NaN |
| `info.json` | metadata (see below) | | |

Sources: [SAMPLE], plus `_NODATA = dict(IVH=0.0, IVV=0.0, MNA=0, MLU=3)` in [GH `catalogue.py`]. All 67,490 labelled grids list exactly these 9 datasets. [PKL]

- The date in each file name is the acquisition date of that image. MK0 files carry the MS1 date.
- In unlabelled grids the AOI field in the file names is `NA`, and there are **no `MK0_MLU` and no `MK0_DEM` files**. Only the 6 SAR files, `MK0_MNA` and `info.json` are present. [SAMPLE] `catalogue.py` adds MLU only `if self.inaoi`.

**Raster properties.** These are identical across all files of both inspected grids. [SAMPLE]

- 1 band, 224 × 224 pixels.
- **EPSG:3857** (Web Mercator, as set by `CRS: 3857` in `catalogue.yaml`), pixel size **10 m**. The GRD SNAP graph uses `pixelSpacingInMeter 10.0` and a WGS 84 / Pseudo-Mercator projection. [GH `grd_preprocessing.xml`]
- Uncompressed, no band descriptions, the only tag is `AREA_OR_POINT=Area`.
- The `info.json` `geom` polygon equals the raster bounds exactly. For example, `7673495 3123355 → 7675735 3125595` (2240 m = 224 × 10 m).

**`info.json` keys:**

- `sources`: per slot (`MS1`, `SL1`, `SL2`) the `source_date`, the Sentinel-1 product UUIDs `s1_ids`, `coverage` and `crank`.
- `datasets`: per file the name, nodata, dtype, ptype, pname, rank and date.
- `grid_id`, `actid`, `aoiid` (int; `null` when unlabelled), `flood_date` (event reference time).
- `pcovered`, `pwater` and `pflood` (% of the grid; `null` when unlabelled), `gvalid`, `slavecov`, `mastercov`, `revision`, `version`, `geom`.

[SAMPLE, PKL]

- 480 of the 67,490 grids have 2 `s1_ids` for MS1, which means the post-event tile is a mosaic of two products. [PKL]
- In 2,502 grids `pcovered` is below 100. [PKL]

## 3. Backscatter units and nodata

- **Linear power (σ⁰, not dB, not scaled integers).** All SAR values are ≥ 0. For example, the labelled grid's MS1 VV median is 0.127, or −9.0 dB, and its VH median is 0.029, or −15.3 dB. Converted with 10·log10, the 1st and 99th percentiles fall at about −30 to −3 dB. [SAMPLE]
- The SNAP graph confirms these units:
  - `Calibration` with `outputSigmaBand=true` and `outputImageScaleInDb=false`.
  - `Terrain-Correction` with `applyRadiometricNormalization=false`.
  - So the output is sigma0 on the ellipsoid, terrain-geocoded but not terrain-flattened. [GH `grd_preprocessing.xml`]
- **Nodata is 0.0.** It coincides with `MK0_MNA == 0`. In the unlabelled grid, 27,616 pixels are 0 in every SAR band, and the same 27,616 pixels are 0 in MNA. [SAMPLE]
- **`MK0_MNA` is the valid-data mask: 1 = valid, 0 = no data.** It is computed from the master (MS1) image (`GridProduct(gd=self.gdmaster, pname="MNA")`). [GH `catalogue.py`]
  - `Dataset.update_min_max_stats` uses it as `invalid_mask = valid_mask != 1`. [GH `Dataset.py`]
  - The webdataset exports it as `valid_mask.npy` ("0: invalid, 1: valid"). [HF-WDS card]
- How the KuroSiwo code feeds models: it reads the linear values, clamps them to `[0, 0.15]` (`clamp_input`, about −8.2 dB), replaces NaN with 0.15, and normalises with `data_mean [0.0953, 0.0264]` and `data_std [0.0427, 0.0215]` (VV, VH). [GH `Dataset.concat`, `data_config.json`; PAPER suppl. §5 "clipped at a max value of 0.15 and normalized"]
- DEM: NaNs are interpolated (`rio.interpolate_na`), then the DEM is normalised with `dem_mean 93.4313` and `dem_std 1410.8382`. [GH `Dataset.py`, `data_config.json`]

## 4. Labels (`MK0_MLU`)

- Values: **0 no water, 1 permanent water, 2 flood, 3 invalid** (the nodata value).
  - `CLASS_LABELS = {0: 'No water', 1: 'Permanent Waters', 2: 'Floods', 3: 'Invalid pixels'}` in [GH `change_detection_trainer.py`].
  - Every loss and metric uses `ignore_index=3`. [GH `utilities.py`]
  - The dataset card lists 0/1/2 for `mask.npy`. [HF-WDS]
- Labels come from manual photointerpretation by five SAR experts at 1:1000 scale, starting from CEMS shapefiles where those existed. [PAPER §3, suppl. §4]
- The KuroSiwo `Dataset` **does not return `MNA` to the loss**. It is only used for optional min-max scaling and for augmentation. So invalid pixels are excluded from training only where MLU = 3. [GH `Dataset.__getitem__`]
- Both inspected grids were all-valid, so this research did not check that MLU = 3 wherever MNA = 0.
- Both labelled grids in the sample were all 0 (no water). The 1/2 values were not seen in the sample. They are confirmed by code and cards only.

## 5. Dates: MS1, SL1, SL2

- MS1 is the post-event image; SL1 and SL2 are the two pre-event images. [GH file reading in `Dataset.__getitem__`: `MS1→flood`, `SL1→pre_event_1`, `SL2→pre_event_2`; HF cards: `sec1` = "first pre-flood image"]
- Measured over all 67,490 labelled grids [PKL]:
  - **SL2 < SL1 < flood_date ≤ MS1 in every grid.** SL1 and SL2 are always pre-event, and SL1 is the more recent of the two.
  - MS1 − flood_date: 0 to 43 days, median 1. The paper gives a mean of 3.6 days, std 6.07 and median 1. [PAPER §3]
  - flood_date − SL1: 6 to 671 days, median 83 (per grid; dominated by Pakistan).
  - SL1 − SL2: 6 to 252 days, median 12.
  - The pre-event dates are "with varying temporal distances". [PAPER §3]
  - Per activation there are usually a single (MS1, SL1, SL2) date triple. Activations 118, 279 and 567 have 2 to 3 triples across their AOIs. [PKL]
- All three images of an event share one orbit direction, ascending or descending. [PAPER §3]
- The SL1 − SL2 = 12 days that is common in the data suggests the same relative orbit. `info.json` does not record the relative orbit; it can be recovered from `s1_ids`.

## 6. Splits

- The splits are **lists of activation ids** in `configs/train/data_config.json` [GH]. The paper calls them "10 flood events ... as testing sites". [PAPER §3]
  - `train_acts` (27): 130, 470, 555, 118, 174, 324, 421, 554, 427, 518, 502, 498, 497, 496, 492, 147, 267, 273, 275, 417, 567, 1111011, 1111004, 1111009, 1111010, 1111006, 1111005
  - `val_acts` (7): 514, 559, 279, 520, 437, 1111003, 1111008
  - `test_acts` (10): 321, 561, 445, 562, 411, 1111002, 277, 1111007, 205, 1111013
- **Activation 514 (Guyana) is not in the data.** Both pickles hold 43 activations, and 514 is not one of them. In practice, val is 6 events. `catalogue.yaml` lists 45 activations, including 514 and 1111012 (Australia, Singleton), and neither appears in the pickles. [PKL, GH `catalogue.yaml`]
- The default `track` is `"RandomEvents"`, so membership is by `actid` alone. The `"Climatic"` track uses `"<actid>_<aoi:02d>"` keys. [GH `Dataset.py`]
- **What the pickles add** [PKL; PAPER suppl. §5]:
  - `KuroV2_grid_dict.gz` ("train pickle", 31,707 grids) is exactly the grids with `pwater > 0` or `pflood > 0`. This matches the paper's "at training time we skip samples that do not contain water".
  - `KuroV2_grid_dict_test_0_100.gz` (67,490 grids) contains every labelled grid. Train uses the train pickle filtered to `train_acts`, which gives 21,462 grids. Val and test use the full pickle filtered to their activations, which gives 2,826 and 15,896 grids. This matches "We test on all available samples of the test areas".
- **The pickles are not needed.** Each grid's own `info.json` carries `actid`, `aoiid`, `pwater` and `pflood`. The rule split = `actid` ∈ list, plus (train only) `pwater + pflood > 0`, reproduces them. The climate zone (`clz`) used for metrics is in `catalogue.yaml` per AOI.
- **The webdataset's train/test folders do not follow the event split** [WDS-HDR, HF-WDS tree]:
  - The first sample of `test_GRD/shard-00000.tar` is activation 118, a train event, with `pflood = 0`.
  - Size arithmetic: 1,819,136 bytes per sample and about 6,000 samples per shard put `test_GRD` at about 67,490 samples, the whole labelled set, and `train_GRD` at about 25,707. A webdataset consumer must still filter by `info.json` `actid`.

## 7. Preprocessing compared with Planetary Computer `sentinel-1-rtc`

| | KuroSiwo GRD [GH xml, PAPER §3] | PC `sentinel-1-rtc` [PC] |
|---|---|---|
| Chain | SNAP: Apply-Orbit-File, ThermalNoiseRemoval, Remove-GRD-Border-Noise, Land-Sea-Mask (SRTM), Calibration, **Lee Sigma speckle filter** (7×7 window, 3×3 target, sigma 0.9), Terrain-Correction | Catalyst: calibration, then **radiometric terrain correction** (Small 2011, "flattening gamma") on PlanetDEM |
| Quantity | **σ⁰** (sigma nought), ellipsoid incidence; no radiometric normalisation | **γ⁰ terrain-flattened** (gamma nought RTC) |
| Speckle filter | yes (Lee Sigma) | none |
| DEM | SRTM 1 Sec, bilinear resampling | PlanetDEM |
| Grid | EPSG:3857, 10 m | 10 m, UTM per item |
| Units | linear power | linear power |
| Sea | masked (`Land-Sea-Mask`, nodata at sea) | not masked |

- The Kuro Siwo inputs differ from this project's Filtered Cube in quantity (σ⁰ vs γ⁰ RTC), speckle filter and CRS. The units match: both are linear before this project's dB conversion.
- Whether the gap matters is a question the map lists under "Not yet specified: Domain gap". This research does not decide it.

## 8. Mapping onto the canonical patch layout (map #45)

Canonical layout: `<root>/dataset.yaml` + `index.parquet` + `samples/<id>/{pre2,pre1,post}.tif` (bands inside, float32 dB, NaN nodata) + `label.tif` (source classes, 255 ignore) + optional `dem.tif`.

| Canonical | From KuroSiwo GRD | Conversion |
|---|---|---|
| `samples/<id>/` | one labelled grid | `id` = `grid_hex` (unique: 67,490 keys = 67,490 grids). Keep `actid`, `aoiid` and the slot dates in `index.parquet`. |
| `post.tif` | `MS1_IVV` + `MS1_IVH` | 2 bands (VV, VH). `dB = 10·log10(x)` where `MNA == 1 and x > 0`, else NaN. |
| `pre1.tif` | `SL1_IVV` + `SL1_IVH` | same; SL1 is the more recent pre-event image |
| `pre2.tif` | `SL2_IVV` + `SL2_IVH` | same; SL2 is the older pre-event image |
| `label.tif` | `MK0_MLU` | keep 0 / 1 / 2 (source classes); **3 → 255**; also set 255 where `MNA == 0` (see ambiguity c) |
| `MK0_MNA` | (no file of its own) | folded in: NaN in the slot rasters and 255 in the label |
| `dem.tif` | `MK0_DEM` | float32 metres, NaN nodata as given |
| CRS, bounds in index | raster profile / `info.json` `geom` | EPSG:3857, 224 × 224, 10 m. Copy the profile, don't reproject. |
| split column | `actid` ∈ `train_acts` / `val_acts` / `test_acts` | lists from `data_config.json`, hard-coded in the converter. The train split optionally drops grids with `pwater + pflood == 0` to match BlackBench. Activation 514 is absent. |
| `dataset.yaml` mean/std | computed on the train split | per band, in dB, valid pixels only. The KuroSiwo published stats are linear and clamped, so they don't apply. |

- **Unlabelled grids** (`/00/` paths: no MLU, no DEM) are out of scope for supervised training. The converter skips them. That does not shorten the download, because they share shards with the labelled grids.
- **Pretrained SNUNet.** Its model card must state `units: linear`, clamp 0.15, and the linear means and stds above. The engine receives dB and converts back (`10^(dB/10)`), so the dB-based canonical layout serves both.

## 9. Open ambiguities

a. **Licence:** CC BY 4.0 (Hugging Face cards, GitHub README) or MIT (paper). Ask the authors, or cite both and follow the stricter (CC BY: attribution).

b. **Download route:** the GeoTIFF release forces about 700 GB of streaming to reach about 100 GB of labelled grids. The alternative is webdataset `test_GRD` (about 123 GB). It appears to hold all 67,490 labelled samples as `.npy` plus `info.json`, but this was inferred from shard sizes and one sample. It has no georeferencing beyond `info.json` `geom` (EPSG:3857 implied), and its `mask.npy` may or may not keep the value 3. Confirm by reading one full shard's `info.json`s before choosing.

c. **Do MLU = 3 and MNA = 0 coincide?** Not verified: both sampled grids were fully valid. The safe rule is the union (255 if either).

d. **`train_GRD` webdataset contents:** about 25,707 samples by size arithmetic, compared with 31,707 in the train pickle. Not resolved, and only relevant if the webdataset route is chosen.

e. **Events with more than one date triple** (118, 279, 567): the slot dates vary by AOI. Store dates per sample, not per event.

f. **Exact linear quantity:** σ⁰ per the SNAP graph. The graph sets `saveSigmaNought=false` in Terrain-Correction, but the Calibration step already outputs sigma0. The values are consistent with σ⁰ (VV median about −9 dB). This matters only for the domain-gap question.
