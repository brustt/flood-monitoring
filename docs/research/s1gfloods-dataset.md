# S1GFloods: format, hosting and labels

Research for #47 (part of #45). Question: what is S1GFloods concretely enough to spec its converter to the canonical
patch layout?

**Short answer.** S1GFloods ships as one 643 MB RAR of flat 8-bit PNGs: `A/` (pre), `B/` (post) and `Label/`, each
`image_1.png` .. `image_5360.png`. Each image is one greyscale band copied into 3 identical RGB channels. The bands have
no units, the polarisation is not stated, and the files have no georeferencing, no event name and no split. The labels
are binary new flood (255) against everything else (0). It **cannot be converted faithfully** to the canonical layout
(float32 dB, `crs` + `bounds`, splits by event). The best a converter can do is a lossy "display-scaled, ungeoreferenced,
no source split" variant, flagged as such in `dataset.yaml`.

Checked on 2026-11-26.

## Sources

| Id | Source | URL |
|---|---|---|
| P | DAM-Net preprint (arXiv 2306.00704; the HTML version was read) | https://arxiv.org/abs/2306.00704 , https://arxiv.org/html/2306.00704 |
| J | Published paper: Saleh, Weng, Holail, Hao, Xia, *ISPRS J. Photogramm. Remote Sens.* 212 (2024) 440-453. Paywalled (HTTP 403 to fetch); only the citation was used | https://www.sciencedirect.com/science/article/abs/pii/S0924271624002168 |
| G | Authors' GitHub repo (default branch `Flood-Mapping`). The real name is `S1GFlood-Detection`, singular "Flood"; `S1GFloods-Detection` gives 404 | https://github.com/Tamer-Saleh/S1GFlood-Detection |
| H | Hugging Face dataset (created 2026-05-23) | https://huggingface.co/datasets/Tamer-Saleh/S1GFloods |
| D | Google Drive copy | https://drive.google.com/file/d/1bm_sFfJ05Fryj6Ib1niIidOywljzIEgo/view |
| B | Baidu copy (not checked) | https://pan.baidu.com/s/1E4dEJtlQ6xeUDRPGO904KQ?pwd=m6gr |

## Hosting, size and licence

- **Three mirrors** are linked from the README [G, README "Datasets"]: Google Drive, Hugging Face and Baidu.
- **Hugging Face** holds a single file, `S1GFloods.rar`, of 673,724,972 bytes (Git LFS). It is not gated
  [H, `https://huggingface.co/api/datasets/Tamer-Saleh/S1GFloods/tree/main`]. Direct download URL:
  `https://huggingface.co/datasets/Tamer-Saleh/S1GFloods/resolve/main/S1GFloods.rar`.
- **Google Drive** holds `S1GFloods.rar`, shown as 643M [D]. 673,724,972 B is 642.5 MiB, so it is very likely the same
  file. Only the name and size were compared, not the bytes.
- **The archive is RAR5** (`file`: "RAR archive data, v5"). It is not solid: the main-header archive flags are `0x00`.
  Extracting it needs `unrar`/`7z`/libarchive, and none of these is installed on the dev machine.
- **The licence statements conflict:**
  - The repo `LICENSE` file is **CC0-1.0** [G, `LICENSE`; GitHub API `license.spdx_id`].
  - The README says "The datasets are released for **non-commercial and research purposes only**. For commercial
    purposes, please contact the authors." [G, README "License"].
  - The HF dataset card says **`license: cc-by-4.0`** [H, `README.md` front matter].
  - The preprint states no dataset licence [P].

  Our use is research, so all three allow it. Cite [J] in any case.

## On-disk structure

The README describes a split layout [G, README "Dataset Preparation"]:

```
SIGFloods/{train,val,test}/{A,B,GT}/<region><year><XY>.png    A = before, B = after, GT = label
```

The **released archive does not follow it.** Its RAR5 headers were read over HTTP range requests, without downloading
the payload, using the quick-open record at the end of the archive plus a walk of the ~10 MB of gaps that record does not
cover. The archive holds exactly **16,080 files and nothing else**:

```
Label/image_{1..5360}.png   5,360 files   (stored first, from byte 29)
A/image_{1..5360}.png       5,360 files   (from byte ~11.2 MB)
B/image_{1..5360}.png       5,360 files   (from byte ~250 MB)
```

- The archive has no top-level folder, no `train`/`val`/`test` folders, no split list, no metadata or CSV, and no event
  or region in the file names.
- File names use `image_<n>.png`, not `<region><year><XY>.png`.
- The unpacked sizes have medians of about 55 KB (A), 106 KB (B) and 5 KB (Label).

The repo carries a 10-pair sample, `S1GFloods_example/{train,val,test}/{A,B,GT}/S1_<n>.png` for n = 5300..5309: train
5300-5305, val 5308-5309, test 5306-5307 [G, tree `S1GFloods_example/`]. **Each sample file is byte-identical to
`image_<n>.png` in the HF archive.** The CRC32 and size of all 12 sample files checked match the CRC32 stored in the RAR
file headers. So the sample is a valid stand-in for the release's format. Its split assignment may only be a demo:
ids 5300-5309 are consecutive, yet they are spread across all three splits.

- **Patch size**: 256 x 256, cut as "non-overlapping image patches with the size of 256x256" [P, Sec. 4 "Data
  Annotation"; G, README]. Confirmed on the sample (`file`: "PNG image data, 256 x 256").
- **Samples per split**: train 4,300, val 530, test 530, for 5,360 pairs in total [P, Sec. 4; G, README "Dataset
  Statistics"]. How the splits were drawn: "each split should contain all flood causes and scenes" [P, Sec. 4]. The
  paper does not say whether the split is by event or by patch, and gives no per-patch assignment.
- **Loader**: `utils/dataloaders.py` lists `<split>/A/`, then opens `A/<name>`, `B/<name>` and `GT/<name>` with PIL
  [G, `utils/dataloaders.py`]. This is the README layout, which the user has to rebuild by hand from the flat release.

## Bands, units and preprocessing

Inspected on the sample with a stdlib PNG decoder (`python3 -I`, scratchpad):

- **A and B are 8-bit RGB PNGs whose R, G and B channels are identical**: one greyscale band, replicated. They have no
  ancillary chunks, so no `tEXt`, `pHYs`, `iCCP` or geo tags.
- **The value ranges suggest a per-image stretch:**
  - A (pre) images reach 255, with up to 3,429 saturated pixels.
  - B (post) images top out at 244-247, never 255.
  - The mean varies from 22 to 167 across patches.

  This points to a display stretch per image or per scene, not a fixed dB-to-byte mapping. This is an inference from 12
  files; the stretch is not documented.
- **What the sources say**, all from [P, Sec. 4]:
  - Imagery is "Sentinel-1 SAR", downloaded from the Alaska Satellite Facility.
  - The labels were made from water masks thresholded at "σ0 = -18 dB for all image pairs" with python-snappy (ESA
    SNAP), so the source data was calibrated σ0 in dB at some stage.
  - The README adds a "spatial resolution: 10 meters" [G, README].
- **What the sources do not say**, in [P] or [G]:
  - The product type (GRD/SLC) and the **polarisation** (VV, VH or a combination).
  - The SNAP steps: orbit, thermal noise, speckle filter, terrain correction and DEM.
  - The dB clip range and the 8-bit scaling.

  A 2025 ISPRS Annals paper describes DAM-Net as using single-polarisation intensity
  (https://isprs-annals.copernicus.org/articles/X-G-2025/745/2025/isprs-annals-X-G-2025-745-2025.pdf, secondary), which
  is consistent with the one-band PNGs. VV is the likely guess, but it is **not confirmed**.
- **The authors' own pipeline does not normalise**: `test_transforms = Compose([ToTensor()])`, and `ToTensor` casts
  the 0-255 RGB to float without scaling. The `Normalize` class (`/255`, mean/std) exists but is unused
  [G, `utils/transforms.py`]. `metadata.json` sets `"num_channel": 3` and `"patch_size": 256` [G, `metadata.json`].

## Labels

- **Values**: `0` = background / non-flooded, `255` = "newly inundated flood area" [G, README "Dataset Statistics"]. The
  paper describes the same thing as binary change, 1 = flooded, 0 = non-change [P, Sec. 3.1]. The loader divides by
  255 to get {0, 1} [G, `utils/transforms.py` `ToTensor`].
- **Confirmed on the sample**: the labels are 8-bit greyscale with only the values {0, 255}. The flood fraction per
  patch is 0.14 to 0.80.
- **Permanent water is in class 0.** Flood and permanent water were separated by comparing the water masks of each
  pre/post pair [P, Sec. 4], and the label marks only *newly* inundated area [G]. Nothing says this explicitly, but it
  follows from those two statements.
- **There is no nodata or ignore value.** No label value other than 0 and 255 occurs.
- **Labelling method**: a -18 dB threshold, then manual correction against Google Earth / Maxar optical imagery
  (removing shadows, airports and vegetated land), then erosion and dilation [P, Sec. 4].

## Events and locations

- **Scope**: the paper and README count "46 flood events, 2015-2022, six continents" [P, abstract and Sec. 4; G, README].
  Table 2 of [P] lists 48 rows: 46 training events plus 2 large external test scenes, Omaha (Nebraska, 2019-03-18) and
  Baluchestan (Iran, 2020-01-13). The external scenes are used only for inundation mapping, not in the 5,360 pairs
  [P, Sec. 4, 5.3].
- **Coverage by continent**: Asia 15, Africa 23, North America 5, Europe 2 (Duisburg and Roermond, July 2021),
  Oceania 2 and South America 1. The source scenes are 1,574 x 2,500 to 12,705 x 17,952 px [P, Table 2].
- **Multiple post images**: some events are marked "∗ multiple post-event images" [P, Table 2], so one event can give
  several pairs with different B dates.
- **Orbit columns**: Table 2 has absolute orbit and path columns, which could in principle recover the scenes.
- **No per-patch mapping**: nothing in the release maps `image_<n>` to an event, a date or an orbit.

## Georeferencing

- **There is none.** The PNGs carry no CRS, transform, world file or coordinates, and the archive holds no sidecar
  files.
- The paper gives place names and dates only, with no footprints or coordinates [P, Table 2].
- The patch-to-scene offsets are not published. The README's `<XY>` suffix hints that such offsets existed before
  release, but the released names drop them.
- So the patches cannot be placed on a CRS, and cannot be merged back into rasters.

## Mapping onto the canonical patch layout (#45 Notes)

Canonical layout:
`<root>/dataset.yaml + index.parquet + samples/<id>/{pre2,pre1,post}.tif` (float32 dB, NaN nodata), plus `label.tif`
(source classes, 255 = ignore) and an optional `dem.tif`. Splits come from the source, by event; mean/std are computed
on the train split; merging needs `crs` + `bounds`.

| Canonical item | S1GFloods | Mappable? |
|---|---|---|
| `samples/<id>` | `<id>` = `image_<n>` stem, n = 1..5360 | yes |
| slot `pre1` | `A/image_<n>.png` | yes |
| slot `post` | `B/image_<n>.png` | yes |
| slot `pre2` | absent | no: S1GFloods supports only `pre1+post` (and `post`-only) models |
| bands per slot (VV, VH) | 1 band of unknown polarisation (take the R channel; G and B are copies) | **partly**: one band, name unknown; no VH |
| units float32 dB | uint8 display values, per-image stretch, scaling unknown | **no**: dB cannot be recovered. The converter could store the values as float32 0-255 (or 0-1) with `units: display_uint8` in `dataset.yaml`, which breaks the "dB everywhere" contract |
| NaN nodata | no nodata marker; 0 is a valid dark pixel | no nodata to map; write no NaN |
| `label.tif` source classes | 0 = background (permanent water included), 255 = new flood | **clash**: the source flood value is 255, the canonical ignore value. The converter must remap 255 → 1 and declare classes `{0: background, 1: flood}`, `positive: 1`. No ignore pixels |
| permanent water class | merged into 0 | not separable, so no 3-class training on S1GFloods |
| `dem.tif` | absent | no |
| `crs`, `bounds` in the index | none | **no**: write null; `--merge` is impossible on this dataset |
| split, by event, from source | train/val/test counts published (4,300/530/530), but no assignment and no event per patch in the release | **no**: no source split can be kept. Options are an author-supplied list (ask the authors), a random split (leaks events, and goes against the map's "no own splits") or using S1GFloods only as an extra eval/test set |
| event / date / orbit in the index | none per patch | no |
| train mean/std per band | computable on whatever split is chosen, in display units | yes, but not comparable with dB stats |

**Verdict.** The converter is mechanically trivial: read PNG channel 0, write single-band GeoTIFFs without a CRS, and
remap the label 255 → 1. But the result is **not faithful** to the canonical contract on three counts: units (not dB),
georeferencing (none) and splits (none). A model card trained on it needs its own `units`/normalisation, and it cannot
take Filtered Cube dB input without a mapping that the dataset does not define.

## Open ambiguities

1. **Polarisation**: VV, VH or something else? Not stated in [P] or [G]. Probably VV; ask the authors
   (tamersaleh@whu.edu.cn) or check the paywalled published version [J].
2. **dB-to-byte scaling**: the clip range, and whether it is per image, per scene or global. The sample suggests per image
   or per scene. Without it, no dB reconstruction and no domain matching to the Filtered Cube is possible.
3. **Split assignment**: which `image_<n>` belongs to train, val or test. Is it id ranges (e.g. 1-4300 / 4301-4830 /
   4831-5360)? Nothing confirms that, and the sample's 5300-5309 spread across all three splits argues against it.
   Is the split by event? Unknown.
4. **Event per patch**: `image_<n>` has no link to the 46 events. This blocks "splits by event" and any per-event
   analysis.
5. **Licence**: CC0-1.0 (repo), non-commercial research only (README) or CC-BY-4.0 (HF). Which one governs the data?
6. **Event count**: 46 (text) against 48 rows in Table 2 (46 + 2 external test scenes, by our reading).
7. **Drive and Baidu copies**: assumed identical to the HF archive (same name, ~same size). Not verified byte-for-byte.
8. **Preprocessing chain**: the terrain correction and speckle filter are unstated, which matters for the domain gap with
   the Planetary Computer RTC Filtered Cube (#45 "Not yet specified").

## Method notes

- **Repo files**: the README, `metadata.json`, the `utils/` loader code and the tree were read via `gh api` on
  `Tamer-Saleh/S1GFlood-Detection@Flood-Mapping`.
- **Sample inspection**: 12 sample PNGs were downloaded to the scratchpad and decoded with a stdlib-only script under
  `python3 -I`.
- **Archive listing without download**: only headers were read, via HTTP range requests on the HF `resolve` URL, about
  11 MB of the 674 MB archive:
  1. The main header (byte 8) holds a locator. Its quick-open offset is relative to the main header and points to the
     quick-open record at byte 673,043,632.
  2. That record caches 10,901 file headers. Their offsets count back from the record.
  3. The remaining 5,179 headers were found by walking the 195 byte gaps between the cached entries.
- **Sample-to-release check**: the CRC32s in the RAR headers were compared with `zlib.crc32` of the sample files.
