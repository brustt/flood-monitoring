# KuroSiwo SNUNet checkpoint: input conventions

Research for issue #46 (map #45). Question: what input conventions does `model/smunet.pt` (KuroSiwo `best_segmentation.pt`, `SNUNet_ECAM(in_channels=3, out_ch=3, base_channel=32)`, epoch 13) expect?

Sources are pinned to commits of https://github.com/Orion-AI-Lab/KuroSiwo. Shorthands:

- `MAIN` = https://github.com/Orion-AI-Lab/KuroSiwo/blob/4347ed173c4e48f5a9d578bb5fe8453706b08e5e (current `main`, 2026-07-16)
- `V1` = https://github.com/Orion-AI-Lab/KuroSiwo/blob/d2750a1f4217285dd3d85b9c993b23304eb3f7cf (initial commit, 2023-12-11)
- Paper v1 = https://arxiv.org/abs/2311.12056v1 (2023-11-18); paper v3 = https://arxiv.org/abs/2311.12056v3 (NeurIPS 2024 D&B, 2024-11-01)

## Short answer

- **Per-branch channels: VV, VH, slope**, in that order. Slope is the Horn rise/run slope of the DEM, worked out on the patch grid. The same slope tensor goes into both branches. It is **not** the raw DEM and **not** VH/VV.
- **Pair: `forward(xA = pre_event_1, xB = post_event)`.** `pre_event_1` is the most recent pre-flood acquisition (SL1). Picking `pre_event_1` is backed by the sources. Putting it in `xA` rather than `xB` is inferred: no source states it.
- **Units:** Sigma0 as **linear power** (not dB, not gamma0). SNAP GRD chain with a Lee Sigma 7x7 filter and Range-Doppler terrain correction, at 10 m in EPSG:3857.
- **Transform:** VV and VH are clamped to [0, 0.15]. NaN becomes 0.15 and stored nodata 0.0 stays 0.0. The bands are then normalized with mean (0.0953, 0.0264) and std (0.0427, 0.0215). DEM NaNs are filled by nearest-neighbour interpolation, then the slope is computed and normalized with mean 2.9482 and std 79.2493 (the v1 stats).
- **Patches:** 224x224. H and W must be multiples of 16.
- **Output:** logits `[B, 3, H, W]`. 0 = no water, 1 = permanent water, 2 = flood. Label 3 (invalid) is ignored in the loss.

## Findings

### 1. When the checkpoint was made, and which code it matches

- The SNUNet download link (the same Dropbox `best_segmentation.pt` URL that is in the README today) was added on 2024-01-04 in commit "add checkpoints", and it has not changed since. Sources: https://github.com/Orion-AI-Lab/KuroSiwo/commit/043a36abc03b4f626f73f3a5e915abc0fc6379da and `MAIN/README.md#L77`.
- The checkpoint is therefore older than the public change-detection (CD) trainer (2024-01-31, https://github.com/Orion-AI-Lab/KuroSiwo/commit/48c9ea74ddd0beac0ae67753547c237503a161c7). It is also older than the CD channel-count rule (2024-02-08, https://github.com/Orion-AI-Lab/KuroSiwo/commit/490dba2babd0f52fed652d63752a8a6fd249ae1e) and than DEM/slope support in CD (2024-02-08, https://github.com/Orion-AI-Lab/KuroSiwo/commit/a0a0e6f2d2e9f427aeda9a7d74cdfcadb1a8f3c5). It was trained on KuroSiwo **v1** with code that is not public. The public code from February 2024 onwards is the closest reconstruction.
- The results it backs are in **paper v1**, Table 2: "SNUNet-CD, Caps. 2, DEM –, Slope ✓" (mIoU 74.73). Source: https://arxiv.org/abs/2311.12056v1, Section 4 / Table 2.
- Paper v2 and v3 retrained on KuroSiwo v2. There the best SNUNet-CD GRD row is "Caps. 2, DEM –, Slope –", which would give 2 channels and does not match this checkpoint. Source: https://arxiv.org/abs/2311.12056v3, Table 2.
- The optimizer state inside the checkpoint matches the public CD trainer with `snunet.json`, and not the paper's table of hyperparameters:
  - Checkpoint: Adam with lr 1e-3, betas (0.9, 0.999), weight decay 0, and a constant `LambdaLR`.
  - Public code: `torch.optim.Adam(model.parameters(), lr=model_configs['learning_rate'])` with default betas (`MAIN/training/change_detection_trainer.py#L54`), `learning_rate: 1e-3` and `lr_schedule: null` (`MAIN/configs/method/snunet/snunet.json#L4-L7`).
  - Paper v3, Table 4, says lr 1e-5.
  - The checkpoint was inspected locally without torch, using a restricted `pickle.Unpickler` over `best_segmentation/data.pkl`.
- Two smaller quirks:
  - The checkpoint's `lr_scheduler_state_dict.last_epoch` is 0 although `epoch` is 13. The public trainer calls `lr_scheduler.step()` every epoch (`MAIN/training/change_detection_trainer.py#L300`), so this is a further sign the training code was not the public one. It does not affect inference.
  - `loss` = 0.1470 (validation loss at the best epoch).

### 2. Three input channels: VV, VH, slope

- In the CD task, the channel count per branch is `len(channels)`, plus 1 if `dem` is set. With `channels: ["vv","vh"]` this gives 2 + 1 = **3**, which matches `conv0_0.conv1.weight = (32, 3, 3, 3)`. Sources: `MAIN/utilities/utilities.py#L377` and https://github.com/Orion-AI-Lab/KuroSiwo/commit/490dba2babd0f52fed652d63752a8a6fd249ae1e.
- `SNUNet_ECAM(configs['num_channels'], configs['num_classes'], base_channel=model_configs['base_channel'])` (`MAIN/models/model_utilities.py#L206-L211`). The config values are `num_classes: 3` (`MAIN/configs/config.json#L14`) and `base_channel: 32` (`MAIN/configs/method/snunet/snunet.json#L8`).
- **The extra channel is the slope:**
  - In the Dataset, when `slope` is true, the `dem` tensor returned is the slope (`MAIN/dataset/Dataset.py#L747-L761`).
  - The trainer appends that tensor to each branch: `torch.cat((pre_event_1, dem), dim=1)` and `torch.cat((post_event, dem), dim=1)` (`MAIN/training/change_detection_trainer.py#L117-L134`).
  - Paper v1 reports SNUNet's best setting as Slope ✓, DEM – (see §1).
- **Order inside a branch is VV, VH, slope.** `concat` stacks `(vv, vh)` (`MAIN/dataset/Dataset.py#L148-L158`, comments `# vv`, `# vh`), then the slope is concatenated last.
- **Ruled out:**
  - **VH/VV ratio:** with `scale_input: "normalize"`, `Normalize(data_mean, data_std)` takes 2-element lists (`MAIN/configs/train/data_config.json#L16-L17`, `MAIN/dataset/Dataset.py#L192-L198`), so a third ratio channel would fail to broadcast. No source reports SNUNet with VH/VV.
  - **Raw DEM:** paper v1 says slope, not DEM.
  - **A `permanent_water_mask` channel:** it existed in the February 2024 channel-count rule (commit 490dba2bab above). No public Dataset or trainer ever read it, and it was removed as "redundant" (https://github.com/Orion-AI-Lab/KuroSiwo/commit/23079906f0f720abddc1eda0853d53786d16c148).
- The first-layer weights do not settle the question by themselves. Per-input-channel L2 norms of `conv0_0.conv1.weight` are 1.54, 1.36 and 1.43, so the third channel is as active as the other two (local inspection).
- **Reproducing the run with today's public code** needs `--inputs pre_event_1 post_event --dem --slope` together with `"task": "cd"`.
  - `main.py` passes `args` to `update_config` only when `--inputs` is given or `--dem` is absent (`MAIN/main.py#L66`).
  - `--dem` and `--slope` are only applied inside that branch (`MAIN/utilities/utilities.py#L357-L362`).
  - The CD trainer asserts exactly 2 inputs (`MAIN/training/change_detection_trainer.py#L19`).
  - The current `data_config.json` repeats the `dem`/`slope` keys (true, then false). The last value wins, so both default to false (`MAIN/configs/train/data_config.json#L24-L31`).

### 3. Which pair, and which is xA

- **Paper:** "For the change detection models we strictly use two captions by selecting only the most recent pre-flood image." Source: https://arxiv.org/abs/2311.12056v1, Section 4 (repeated in v3, Section 4).
- **`pre_event_1` is SL1, and SL1 is the most recent pre-event image:**
  - `pre_event_1 = concat(SL1_IVV, SL1_IVH)`, `pre_event_2 = concat(SL2_*)`, `post_event = concat(MS1_*)` (`MAIN/dataset/Dataset.py#L691-L776`).
  - In the grid index `MAIN/pickle/KuroV2_grid_dict_test_0_100.gz`, all 67,490 samples have `SL1.crank = 1` and `SL2.crank = 2`, with `SL2 date < SL1 date < MS1 date`. These counts come from a local restricted-unpickler scan. That file indexes v2, which keeps the v1 naming.
  - So the pair is **`pre_event_1` + `post_event`**.
- **xA and xB:** the trainer calls `model(*inputs)` in the order of `configs['inputs']` (`MAIN/training/change_detection_trainer.py#L117-L136`). That order came from the `--inputs` list of the run, which is not published.
  - The default list puts the pre-event images before `post_event` (`MAIN/configs/train/data_config.json#L10`).
  - In SNUNet-CD, `xA` is the T1 (earlier) image.
  - So `xA = pre_event_1`, `xB = post_event` is the likely order, but it is **inferred**.
  - The order matters: `forward` is asymmetric. Only `xB` goes through `conv4_0`, and every upsampling path starts from the B branch (`MAIN/models/snunet.py#L118-L153`).

### 4. Units, clamp, normalization, nodata

- **SAR values: Sigma0, linear power.** The GRD SNAP graph has:
  - `Calibration` with `outputSigmaBand=true` and `outputImageScaleInDb=false` (`MAIN/configs/grd_preprocessing.xml#L87-L93`)
  - `Speckle-Filter` Lee Sigma, window 7x7, sigma 0.9, target window 3x3 (`#L97-L114`)
  - `Terrain-Correction` on SRTM 1Sec at 10 m in EPSG:3857, with `applyRadiometricNormalization=false` (`#L117-L164`)
  - The paper describes the same chain: "radiometrically calibrated SAR backscatter" (https://arxiv.org/abs/2311.12056v3, Section 3).
  - The Dataset reads the GeoTIFFs as they are (`cv.imread(..., IMREAD_ANYDEPTH)`), with `uint8: false` (`MAIN/dataset/Dataset.py#L669-L729`, `MAIN/configs/train/data_config.json`).
- **Clamp:** `torch.clamp(image, min=0.0, max=0.15)`, then `torch.nan_to_num(image, 0.15)` (`MAIN/dataset/Dataset.py#L164-L166`; `clamp_input: 0.15`, `V1/configs/train/data_config.json#L10`).
  - NaN therefore becomes **0.15**.
  - 0.15 linear is about −8.24 dB.
  - The paper says the same: "All SAR images were clipped at a max value of 0.15 and normalized to 0 mean and 1 standard deviation" (https://arxiv.org/abs/2311.12056v3, Appendix §5).
- **Stored nodata:** every IVV and IVH dataset in the grid index has `nodata: 0.0` (local scan of `KuroV2_grid_dict_test_0_100.gz`). Zeros survive the clamp as **0.0**, so invalid SAR pixels reached the network as 0.0, not as NaN.
  - Invalid pixels are flagged by `MK0_MNA` (valid mask) and labelled 3 in `MK0_MLU` (`nodata: 3`).
  - Label 3 is ignored by the loss (`MAIN/utilities/utilities.py#L317-L321`, `ignore_index=3`).
- **Normalize SAR:** `torchvision.transforms.Normalize(data_mean, data_std)`, with mean `[0.0953, 0.0264]` and std `[0.0427, 0.0215]` for (VV, VH). It is applied after the clamp, the same way to the pre and post images (`MAIN/dataset/Dataset.py#L192-L198`, `#L812-L822`). These values are **the same in v1 and on main** (`V1/configs/train/data_config.json#L12-L13`, `MAIN/configs/train/data_config.json#L16-L17`). The 2025 "update mean/std" commit only changed DEM, slope and SLC stats (https://github.com/Orion-AI-Lab/KuroSiwo/commit/1663f3a12d762f74974d5e28389652d7c5e1e599).
- **DEM to slope:**
  - DEM NaNs are filled with `rioxarray.interpolate_na()` (`MAIN/dataset/Dataset.py#L733-L736`). Its default method is `"nearest"` via `scipy.interpolate.griddata` (https://github.com/corteva/rioxarray/blob/35d411a44834917fdd95ff8a4dbe1ae48e3c00ec/rioxarray/raster_array.py#L914-L918).
  - Then `richdem.TerrainAttribute(rd.rdarray(dem.squeeze(), no_data=nodata), attrib="slope_riserun")` runs (`MAIN/dataset/Dataset.py#L747-L752`).
  - No geotransform is passed, so richdem uses a **1x1 cell** (https://github.com/r-barnes/richdem/blob/9a1c97bb2529e386451a7dd54768b8b74bb4d620/wrappers/pyrichdem/richdem/__init__.py#L216-L222).
  - `slope_riserun` is Horn (1981): `sqrt(dzdx² + dzdy²)` with `dz/dx = ((c+2f+i) − (a+2d+g)) / 8 / cellLengthX` (https://github.com/r-barnes/richdem/blob/9a1c97bb2529e386451a7dd54768b8b74bb4d620/include/richdem/methods/terrain_attributes.hpp#L245-L257).
  - The unit is therefore **metres of elevation per pixel** (10 m pixels): 10x the true rise/run, and not a percentage.
  - At the patch border, missing neighbours take the centre value (ArcGIS-style, `terrain_attributes.hpp#L176-L200`). Nodata cells become −9999 (`TerrainProcessor`, `#L334-L352`; `__init__.py` result `no_data=-9999`).
  - Slope is computed **per 224x224 patch**, so patch edges have one-sided gradients.
- **Normalize slope:** `Normalize(slope_mean, slope_std)`. The v1 values, the ones in force when the checkpoint was made, are **2.9482 / 79.2493** (`V1/configs/train/data_config.json#L18-L19`). Main has 2.1277 / 67.5048, recomputed for v2 in 2025 (`MAIN/configs/train/data_config.json#L26-L27`). Slope is not clamped.
- Training used `mixed_precision: true` (autocast) (`MAIN/configs/config.json`). Running inference in fp32 is fine.

### 5. Patch size and padding

- KuroSiwo tiles are **224x224** at 10 m (paper v3, Section 3: "stored as 224 × 224 tiles"). Each tile geometry in the grid index is 2240 m wide, and `mask = np.zeros((224, 224))` is the fallback (`MAIN/dataset/Dataset.py#L780`).
- SNUNet max-pools 4 times (`MaxPool2d(2, 2)`, `MAIN/models/snunet.py#L73`, `x4_0B` at 1/16). It upsamples with `ConvTranspose2d(stride=2)` and concatenates skip connections, so **H and W must be multiples of 16**. 224 = 14 × 16.

### 6. Outputs

- `forward` returns raw logits from `conv_final = Conv2d(128, 3, 1)`, with no softmax (`MAIN/models/snunet.py#L108`, `#L150-L153`). The checkpoint confirms `conv_final.weight = (3, 128, 1, 1)`.
- Classes are `{0: 'No water', 1: 'Permanent Waters', 2: 'Floods', 3: 'Invalid pixels'}`. Class 3 is a label only and is never predicted (`MAIN/training/change_detection_trainer.py#L15`). The prediction is `output.argmax(1)` (`#L152`).

## Proposed model card values

The card is authored in Python (hydra-zen `store`). This block lists the values; the field names follow the map's model contract.

```yaml
net: SNUNet_ECAM(in_channels=3, out_ch=3, base_channel=32)   # models/snunet.py, MIT
weights: {path: model/smunet.pt, format: torch, key: model_state_dict}
adapter: SiameseTorch            # forward(xA, xB): one tensor per slot
inputs:
  slots: [pre1, post]            # xA = pre1 (most recent pre-event), xB = post
  bands: [VV, VH, slope]         # per slot, in this order; slope is the same tensor in both slots
  units:
    VV: sigma0 linear power      # engine gets dB: x = 10 ** (dB / 10)
    VH: sigma0 linear power
    slope: Horn rise/run on a 1x1 cell = metres of elevation per 10 m pixel, from the DEM
  nodata_fill:                   # applied in linear units, before clamp
    VV: 0.0                      # KuroSiwo stores invalid SAR pixels as 0.0 (the code would turn NaN into 0.15)
    VH: 0.0
    DEM: nearest-neighbour interpolation of NaN, before slope
  clamp: {VV: [0.0, 0.15], VH: [0.0, 0.15], slope: null}
  normalize:                     # (x - mean) / std after clamp
    mean: [0.0953, 0.0264, 2.9482]
    std:  [0.0427, 0.0215, 79.2493]
  patch_size: 224                # H, W multiple of 16
outputs:
  kind: logits                   # [B, 3, H, W]; softmax over dim 1
  classes: {0: no water, 1: permanent water, 2: flood}
  ignore_label: 3                # invalid; training only
  positive: 2                    # flood
  nodata: predictions on input nodata pixels are set to nodata (class 3 was never learnt)
provenance:
  source: https://github.com/Orion-AI-Lab/KuroSiwo (README "Pretrained models" -> SNUNet, added 2024-01-04)
  paper: https://arxiv.org/abs/2311.12056v1 Table 2 (SNUNet-CD, 2 captions, slope; mIoU 74.73)
  dataset: KuroSiwo v1 GRD
  epoch: 13
```

## Ambiguous or unverified

1. **xA/xB order** (pre1 in A, post in B) is inferred, not stated, and it matters because `forward` is asymmetric. Suggested check for the implementation ticket: run both orders on a few KuroSiwo test patches with known labels and keep the order with the much higher flood IoU.
2. **The third channel is slope** according to paper v1. The checkpoint was trained with non-public code from before February 2024, so the slope recipe (richdem on a 1x1 cell, per patch, v1 stats 2.9482 / 79.2493) is reconstructed from code published later. The v1 Dataset slope code is identical to main's. The CD wiring is from February 2024.
3. **Which slope stats:** v1 (2.9482 / 79.2493) matches the checkpoint's date; main's 2.1277 / 67.5048 are for v2. The std of about 79 is large next to a mean of about 3, which suggests the stats include outliers (for example −9999 edge or nodata cells). Either way, the normalized slope is close to constant, so this choice probably has little effect.
4. **Slope on our grid:** KuroSiwo slope is per pixel of the EPSG:3857 10 m grid. Our grid will differ (different CRS, ground pixel size, and DEM source), so the values will not match exactly.
5. **Nodata fill:** the code turns NaN into 0.15, but KuroSiwo stores nodata as 0.0, so 0.0 is what the network saw on invalid pixels. Both regions were ignored in the loss, so either value works as long as predictions there are masked.
6. **Domain gap** (map item "Not yet specified"): KuroSiwo is SNAP Sigma0 with ellipsoid-based Range-Doppler terrain correction and a Lee Sigma 7x7 filter. The pipeline's Planetary Computer RTC is terrain-flattened gamma0 with a different speckle filter. The clamp and normalization above assume Sigma0-like values.
7. The paper's hyperparameters (lr 1e-5, CE+Dice loss) do not match the checkpoint's optimizer state (lr 1e-3, constant schedule). This does not affect inference, but the paper tables are not a reliable record of how this file was trained.
