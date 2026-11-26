# Research: TorchGeo, smp and Lightning for the two models and tiled inference (#49)

Part of map #45. Researched 2026-11-26 against the latest releases on PyPI. The sources are the tagged source trees (TorchGeo `v0.10.1`, segmentation-models-pytorch `v0.6.0`, hydra-zen `v0.17.2`), release notes, PyPI JSON metadata, the official docs, and a smoke test in a throwaway CPU venv. Nothing was installed into the project venv, and `pyproject.toml` / `uv.lock` are unchanged.

Abbreviations: **TG** = TorchGeo, **smp** = segmentation-models-pytorch, **L** = Lightning. "Smoke test" means a script run in a scratch venv with the recommended CPU set (torch 2.15.0+cpu, numpy 2.5.5). It ran `Trainer(fast_dev_run=True)` on fake `[T, C, H, W]` patches.

## 1. Latest versions and their requirements (PyPI metadata, 2026-11-26)

| Package | Latest | Released | `requires_python` | Relevant requirements |
|---|---|---|---|---|
| torch | 2.15.0 | 2026-10-28 | >=3.11 | Linux PyPI wheel pulls `cuda-toolkit[...]==13.2.2`, `nvidia-cudnn-cu13`, `nvidia-nccl-cu13`, `triton~=3.9.0` |
| torchvision | 0.29.2 | 2026-10-28 | >=3.10, !=3.14.1 | `torch>=2.14.0`, `numpy` (no cap) |
| lightning | 2.6.6 | 2026-09-10 | >=3.10 | `torch>=2.1.0,<4.0`, `torchmetrics>0.7.0,<3.0` |
| torchgeo | 0.10.1 | 2026-09-29 | **>=3.12** | `numpy>=1.26`, `rasterio>=1.4.3`, `segmentation-models-pytorch>=0.5`, `kornia>=0.8.2`, `lightning>=2.4` (excluding 2.5.0/2.5.5/2.5.6), `timm>=1.0.3`, `torchmetrics[detection]>=1.5`, `lightly>=1.5.1`, `jsonargparse[jsonnet,signatures]>=4.39`, `geopandas>=1.0`, `pyogrio>=0.10` |
| segmentation-models-pytorch | 0.6.0 | 2026-10-26 | >=3.10 | `numpy>=1.21.2`, `torch>=1.11`, `timm>=0.9`, `huggingface-hub>=0.24`, `safetensors` |
| hydra-zen | 0.17.2 | 2026-10-21 | >=3.9 | `hydra-core>=1.2.0`, `omegaconf>=2.2.3`. Extra `pydantic` → `pydantic>=2.0.0` |
| hydra-core | 1.3.8 (stable), 1.4.0rc3 (pre) | 2026-10-25 | – | `omegaconf>=2.2,<2.4`, `antlr4-python3-runtime==4.9.*` |
| scikit-learn | 1.9.1 | 2026-09-10 | >=3.11 | `numpy>=1.24.1`, `scipy>=1.10.0` |
| kornia | 0.9.2 | 2026-11-16 | >=3.11 | `torch>=2.5.1`, `numpy>=1.21` |
| torchmetrics | 1.9.0 | 2026-03-09 | >=3.10 | `numpy>1.20.0` |
| timm | 1.0.31 | 2026-11-03 | >=3.10 | – |

Source: `https://pypi.org/pypi/<name>/json`, fields `info.version`, `info.requires_python`, `info.requires_dist`, `releases[*].upload_time`.

No package in the stack caps numpy below 2.5, and none needs Python above 3.12. TG itself needs Python >= 3.12, which matches our floor. TG pins its own dev lockfile (`torchgeo/uv.lock` at `v0.10.1`) to **kornia 0.8.3**, so 0.9.x was not tested by TG's CI.

### Resolution against our lockfile

- `uv pip compile` of {numpy>=2.5.4, our locked rasterio 1.5.2 / pandas 3.0.6 / geopandas 1.2.0 / scipy 1.18.1 / pillow 12.3.0 / matplotlib 3.11.2, torch, torchvision, lightning, torchgeo, smp, scikit-learn, hydra-zen} for Python 3.12 resolves cleanly. Both the default index and `--torch-backend cpu` work.
- I copied this repo's `pyproject.toml` and `uv.lock` into a scratch directory, changing only the `eo-pipe` path to an absolute one. I added the candidate groups from §6, and `uv lock` (uv 0.10.7) resolved 230 packages (164 today). **No package already in `uv.lock` changed version or was removed.** numpy stays 2.5.4, and pydantic 2.14.1 comes in through `hydra-zen[pydantic]`.
- The ml group adds these transitive dependencies worth knowing about: `lightly`, `jsonargparse` and `jsonnet` (TG's CLI), `pycocotools` (torchmetrics detection extra), `timm`, `huggingface-hub`, `kornia` and `kornia-rs`, `einops`, `aiohttp` and friends (`fsspec[http]` from Lightning).

## 2. TorchGeo tasks (formerly "trainers")

**Rename in 0.10:** `torchgeo.trainers` became `torchgeo.tasks`, and the `Task` suffix was dropped. `SemanticSegmentationTask` is now `torchgeo.tasks.SemanticSegmentation`, and `ChangeDetectionTask` is now `torchgeo.tasks.ChangeDetection`. The old names still exist as `@deprecated` subclasses in `torchgeo/trainers/__init__.py`. (TG v0.10.0 release notes, "Backwards-incompatible changes"; `torchgeo/trainers/__init__.py` at v0.10.1.) New code should import from `torchgeo.tasks`.

Both tasks are `ClassificationMixin, BaseTask` (`torchgeo/tasks/{segmentation,change,mixins,base}.py`):

- `BaseTask.__init__` calls `save_hyperparameters(ignore='weights')`, then `configure_models`, `configure_losses` and `configure_metrics`. A checkpoint therefore reloads with `Task.load_from_checkpoint(path)`, but the `weights` argument is not saved.
- Optimizer: AdamW(lr) plus ReduceLROnPlateau(patience) monitoring `val_loss`, all hard-coded in `BaseTask.configure_optimizers`. Changing it means overriding that method.
- Metrics (`ClassificationMixin.configure_metrics`): torchmetrics Accuracy, Precision, Recall, F1Score and JaccardIndex, each micro (`Overall*`) and macro (`Average*`), plus per-class versions when `task != 'binary'`. They are logged as `train_*`, `val_*` and `test_*`, and `ignore_index` is passed through to them.
- Losses (`ClassificationMixin.configure_losses`): `ce` → `nn.CrossEntropyLoss(ignore_index, weight=class_weights)`. `bce` → `nn.BCEWithLogitsLoss(pos_weight)`, **which ignores `ignore_index`**. `jaccard` → smp `JaccardLoss`, which drops `ignore_index` unless the task is multiclass. `focal` → smp `FocalLoss(ignore_index)`. `dice` → smp `DiceLoss(ignore_index)`.
  - Smoke test: with logits = 1, `BCEWithLogitsLoss` on targets containing 255 returns **−62.4** where it should return 1.31. **Our labels use 255 = ignore, so `loss='bce'` is wrong for them.** For binary + ignore, use `loss='focal'` or `'dice'`, or use `task='multiclass', num_classes=2, loss='ce', ignore_index=255`. The `ce` route also gives `class_weights`, but the model then outputs 2 logits and the positive score is `softmax[:, 1]`.

### `SemanticSegmentation` (Unet-ResNet18)

Signature (v0.10.1): `SemanticSegmentation(model='unet', backbone='resnet50', weights=None, in_channels=3, task='multiclass', num_classes=None, num_labels=None, labels=None, num_filters=3, pos_weight=None, loss='ce', class_weights=None, ignore_index=None, lr=1e-3, patience=10, freeze_backbone=False, freeze_decoder=False)`. Models: `unet`, `deeplabv3+`, `fcn`, `upernet`, `segformer`, `dpt`.

- `model='unet', backbone='resnet18'` builds `smp.Unet(encoder_name='resnet18', encoder_weights='imagenet' if weights is True else None, in_channels, classes)`. `weights` can also be a TG `WeightsEnum`, its string name, or a Lightning checkpoint path (encoder only, through `tasks/utils.extract_backbone`).
- Input `[B, C, H, W]`. A 5-D `[B, T, C, H, W]` input is still flattened to `[B, T*C, H, W]` but emits a `DeprecationWarning` ("will be removed in a future release. Use SpatioTemporalSegmentation instead"), as confirmed by the smoke test. **Our early-fusion adapter should flatten the slots itself** and pass `in_channels = T × C`.
- Mask shape: binary `y_hat` is `.squeeze(1)` → `[B, H, W]`, so the **mask must be `[B, H, W]`**. A `[B, 1, H, W]` mask fails in torchmetrics with a shape mismatch (smoke test).
- `predict_step` returns `{'probabilities': sigmoid/softmax, 'bounds', 'transform'}` (changed from Tensor in 0.9).
- Binary, focal, ignore 255, resnet18, `in_channels=4` and multiclass ce with `num_classes=3` and ignore 255 both ran a `fast_dev_run` (smoke test). The second one covers the later 3-class ticket.

### `ChangeDetection` (FC-Siam-conc): it exists, `.. versionadded:: 0.8`

Signature (v0.10.1): the same arguments as above, except that the defaults are `model='unet'`, `backbone='resnet50'`, `task='binary'` and `loss='bce'`. Models: `unet`, `deeplabv3+`, `fcn`, `upernet`, `segformer`, `dpt`, `fcsiamdiff`, `fcsiamconc`, `changevit`, `btc`. Binary, multiclass and multilabel are all supported (docstring: "Supports binary, multiclass, and multilabel change detection").

- Input is always `batch['image']` of shape `[B, T, C, H, W]`, and `in_channels` is **per image**. For `fcsiam*` and `changevit` the 5-D tensor goes to the model unchanged. For the others it is flattened to `[B, T*C, H, W]`, but the network is built with `in_channels * 2`. In short, **T = 2 is hard-coded**: `unet` with T = 3 fails with a channel mismatch (smoke test). The docs table lists the input as R^(2×C×H×W) (https://docs.torchgeo.org/en/stable/api/tasks.html).
- Mask shape: `y_hat` is not squeezed, so in binary mode the **mask must be `[B, 1, H, W]`**, the opposite of `SemanticSegmentation` (smoke test). TG's own change datasets return `[1, H, W]` "because VideoSequential requires time dimension" (`datasets/levircd.py::_load_target`). In multiclass mode the mask is `.squeeze(1)`, so `[B, H, W]` and `[B, 1, H, W]` both work.
- `predict_step` returns a plain Tensor of probabilities, not the dict that `SemanticSegmentation` returns.
- Per-batch validation figures are drawn only when the logger has `add_figure` (TensorBoard), and they call `datamodule.plot`. A dataset without `plot` simply skips this.
- `model='fcsiamconc', backbone='resnet18', in_channels=2, task='binary'` with each of `bce`/`focal`/`dice`/`jaccard` and `ignore_index=255` ran a `fast_dev_run` (smoke test). Only focal and dice actually honour the 255.

### `torchgeo.models.FCSiamConc` / `FCSiamDiff` (`torchgeo/models/fcsiam.py`)

- `FCSiamConc(encoder_name='resnet34', encoder_depth=5, encoder_weights='imagenet', decoder_use_batchnorm='batchnorm', decoder_channels=(256,128,64,32,16), decoder_attention_type=None, in_channels=3, classes=1, activation=None)`. It subclasses smp `SegmentationModel` and has one shared smp encoder plus an smp `UnetDecoder`, with skip channels doubled.
- `FCSiamDiff(*args, **kwargs)` subclasses `smp.Unet` (with `aux_params=None`). Its skips are `features2 - features1`.
- `forward(x)`: `x` is `(b, t, c, h, w)`. It uses **only `x[:, 0]` and `x[:, 1]`**, concatenates as `[post_feat, pre_feat]` (index 1 first), and takes the first skip from slot 1. With T = 3 it silently ignores the third slot: the output equals the output on `x[:, :2]` (smoke test). Slot order must therefore be `(pre, post)`, and `pre2 + pre1 + post` needs a custom N-branch variant.
- **Pretrained weights:** TG publishes no FC-Siam checkpoints (no entry in `models/api.py`), only the smp encoder's `encoder_weights='imagenet'`. The smp `resnet18.imagenet` weights now come from the Hugging Face Hub (`smp-hub/resnet18.imagenet`). With `in_channels=2` smp keeps the pretrained first conv, cycling and rescaling the RGB kernels (`encoders/_utils.patch_first_conv`): conv1 becomes `(64, 2, 7, 7)` (smoke test, downloaded from HF). For `in_channels > 3` the kernels are cycled the same way (`weight[:, i % 3]`, scaled by `3/new_in_channels`). The docstring there says "random kaiming", but the code does not do that.
- TG has SAR-pretrained **ResNet50** weights (`ResNet50_Weights.SENTINEL1_GRD_{DECUR,MOCO,CLOSP,GEOCLOSP,SOFTCON}`, `in_chans: 2`). It has **none for ResNet18** (`torchgeo/models/resnet.py`).
- smp 0.6.0 breaking changes that matter here: encoder weights come **only** from the HF Hub (set `HF_ENDPOINT` or load locally when offline), and Python 3.10+ is required. Also new: `model.freeze_encoder()`, and `JaccardLoss(ignore_index)` (smp v0.6.0 release notes). TG 0.10.1 was released before smp 0.6.0, but all the smp APIs it calls (`UnetDecoder(encoder_channels, decoder_channels, n_blocks, use_norm, attention_type, add_center_block)`, `SegmentationHead`, `get_encoder`) are unchanged in 0.6.0 (`decoders/unet/decoder.py`), and the smoke test passes.

### Which task for which model

| Model | Task | Notes |
|---|---|---|
| Unet-ResNet18 (smp), early fusion | `torchgeo.tasks.SemanticSegmentation(model='unet', backbone='resnet18', in_channels=T*C, task='binary', loss='focal' or 'dice', ignore_index=255)` | The adapter or datamodule flattens `[T,C,H,W]` → `[T*C,H,W]`. Mask `[H,W]`. Works for any T. |
| FC-Siam-conc, `pre1`+`post` | `torchgeo.tasks.ChangeDetection(model='fcsiamconc', backbone='resnet18', in_channels=C, task='binary', loss='focal' or 'dice', ignore_index=255)` | Input `[T=2,C,H,W]`, mask `[1,H,W]`. |
| FC-Siam-conc, `pre2`+`pre1`+`post` | **Custom**: a small `FCSiamConc` subclass whose `forward` encodes every slot and concatenates T skips (decoder skip channels × T), plugged in with a `ChangeDetection` subclass that overrides `configure_models` | TG hard-codes T = 2. Not needed for the first runs if they use `pre1`+`post`. |

No fully custom LightningModule is needed for the first two models. The TG tasks cover loss, metrics, optimizer, checkpoint hparams and `predict_step`. Subclass only for: a different optimizer or scheduler, T = 3 FC-Siam, or a unified `predict_step` output (the two tasks return different types).

## 3. Datamodule for our patch layout (`NonGeoDataModule`)

`torchgeo.datamodules.NonGeoDataModule(dataset_class, batch_size=1, num_workers=0, **kwargs)` (`datamodules/geo.py`):

- `setup(stage)` instantiates `dataset_class(split='train'|'val'|'test', **kwargs)`, so our Dataset needs a `split` argument that maps to the split column of `index.parquet`. `collate_fn = default_collate`, so samples must be `dict[str, Tensor]`, which is TG's own sample contract since 0.10 (release notes).
- Augmentation hooks: the class attributes `mean`/`std` (defaults 0/255) feed the default `self.aug = K.AugmentationSequential(K.Normalize(mean, std), data_keys=None, keepdim=True)`. `train_aug`, `val_aug`, `test_aug` and `predict_aug` override it per split. Augmentation runs **on the device after transfer** (`BaseDataModule.on_after_batch_transfer`), picking `{split}_aug` and falling back to `aug`. `prepare_data` only downloads if `download=True`, so it is a no-op for us.
- `plot` delegates to `dataset.plot` if it exists (used by the TensorBoard figures).

**Multi-temporal augmentation: do not use `K.VideoSequential` with masks.** TG's change datamodules (LEVIR-CD, OSCD) wrap the ops in `K.VideoSequential` with a `[B,1,H,W]` mask:

- With kornia 0.9.2 (what uv resolves today) that raises `ValueError: Video and mask must have the same number of frames` (smoke test). The TG LEVIR-CD datamodule is therefore broken on kornia 0.9.
- With kornia 0.8.3 (TG's tested version) it raised `IndexError` in our test as soon as a random op was added.
- TG issue [#4188](https://github.com/torchgeo/torchgeo/issues/4188) (open, 2026-10-12) reports the same pattern silently cropping image and mask differently. Its maintainers suggest stacking the slots on channels, running the augmentations, and unstacking.

The smoke test confirmed that approach. Fold `[B,T,C,H,W]` → `[B,T*C,H,W]`, run a plain `K.AugmentationSequential(K.Normalize(mean.repeat(T), std.repeat(T)), K.RandomHorizontalFlip(), K.RandomVerticalFlip(), K.RandomCrop(...), data_keys=None, keepdim=True)`, then unfold. Image and mask stay pixel-aligned for both `[B,1,H,W]` and `[B,H,W]` masks, and every slot gets the same transform. This one wrapper serves both models.

- NaN nodata survives `K.Normalize` (smoke test). The datamodule or adapter must fill NaN (card's `nodata fill`) **before** normalizing, and should map those pixels to 255 in the mask.
- `MMFloodDataModule` (Sentinel-1 VV/VH ± DEM, `GeoDataModule`) is a useful reference for SAR augmentations: `RandomResizedCrop`, flips, `RandomRotation90`, elastic, and `extra_args={DataKey.MASK: {'resample': NEAREST}}`. It normalizes by median/std.

## 4. Tiled inference and stitching

- **TorchGeo has no stitching code.** The glossary defines "stitching" (`docs/user/glossary.rst`). The sampler docstrings recommend `stride < size` with overlap ≈ the receptive field "to prevent stitching artifacts". The feature request [#30 "Add stitching utilities"](https://github.com/torchgeo/torchgeo/issues/30) (and #1440) is still open.
- TG's tiling tool is `GriddedPatchSampler(dataset, *, size, stride=None, roi=None, units=Units.PIXELS)`. It is new in 0.10 and replaces the now `@deprecated` `GridGeoSampler` (`samplers/spatial.py`, `samplers/single.py`). It needs a TG `GeoDataset` (`RasterDataset` over our slot GeoTIFFs plus an `IntersectionDataset` to pair them). It yields only windows: there is no merge step, and edge handling is the dataset's job.
- smp has no tiling or sliding-window helper (searched the v0.6.0 source).
- `rasterio.merge.merge(sources, ..., method=...)` (rasterio 1.5.2, already locked) supports `first`/`last`/`min`/`max`/`sum`/`count` or a callable `(merged_data, new_data, merged_mask, new_mask, index, roff, coff)`. That is enough for `--patches DIR --merge` from per-patch GeoTIFFs (`crs` + `bounds`): use `sum` plus `count` for a plain average, or a callable for weighted blending.
- Third-party options such as `pytorch-toolbelt` (`ImageSlicer`/`TileMerger`) would add a dependency (it pulls `opencv-python`) for little benefit.

**Recommendation:** keep the engine framework-free (map Notes) and write a small numpy tiler/merger in `flood_monitoring.ml`. It should do the following:
- Compute windows with `rasterio.windows.Window` over the slot rasters, using patch size `P` and overlap `O` (stride `P − O`, last window snapped to the edge).
- Call `predict_proba` on batches of `[B,T,C,P,P]`.
- Accumulate `Σ w·p` and `Σ w` with a 2-D taper weight (e.g. a separable Hann or linear ramp over the overlap, floor > 0) and divide at the end.
- Use the same accumulator for patch folders. `rasterio.merge` with `method='sum'` and `'count'` is an alternative there for unweighted merges.

TG `GriddedPatchSampler` is not worth adopting just for window generation, because it would pull TG's `GeoDataset` indexing into the inference path.

## 5. hydra-zen with Lightning

- `builds(L.Trainer, populate_full_signature=True, ...)` works on Lightning 2.6.6: 42 fields, `to_yaml` OK. So do `builds(torchgeo.tasks.ChangeDetection, populate_full_signature=True, ...)` and `builds(<NonGeoDataModule subclass>, populate_full_signature=True)` (smoke test).
- `zen(train)(Cfg)` and `zen(train, instantiation_wrapper=pydantic_parser)(Cfg)` both instantiated Trainer, task and datamodule and ran `fit` (smoke test). The pydantic parser is `hydra_zen.third_party.pydantic.pydantic_parser`, which needs `hydra-zen[pydantic]` (extra `pydantic>=2.0.0`, hydra-zen `pyproject.toml`). It is wired through `zen(..., instantiation_wrapper=...)` (`wrapper/_implementations.py`) or `instantiate(..., _target_wrapper_=pydantic_parser)`.
- `launch(Cfg, zen(train), version_base="1.3", overrides=[...])` ran a real Hydra job, wrote `<run>/.hydra/config.yaml`, and that file reloaded with `load_from_yaml` + `instantiate(c.task)` into a `ChangeDetection` with the overridden `lr` (smoke test). This supports the map's decision that **the run's `.hydra/config.yaml` is the run's card**.
- Known incompatibilities:
  - **hydra-core ≥ 1.3.6** added a target policy that rejected hydra-zen's `_target_wrapper_` / `zen_wrappers` objects, and `launch(..., multirun=True)` failed on 1.3.7. **hydra-zen 0.17.0–0.17.2 fixes both** (changelog, 0.17.0 "Compatibility"/"Changes" and 0.17.1 regression fix for 1.3.6). Require `hydra-zen>=0.17.2`.
  - hydra-core 1.3.8 (2026-10-25) blocks unsafe PyYAML loaders in `instantiate` (Hydra release notes).
  - Hydra **1.4** (rc3 only, as of now) no longer validates `instantiate` arguments against annotations, and objects instantiated with OmegaConf containers may not be picklable (hydra-zen 0.17.0 changelog, hydra#3537). Stay on hydra-core 1.3.x until 1.4 is final.
  - A custom sweeper must now be a `hydra_plugins` plugin (hydra-zen 0.17.0). The stock basic sweeper is fine for `--multirun`.
- The hydra-zen docs have a Lightning tutorial (`docs/source/tutorials/pytorch_lightning.rst`) using `builds(pl.Trainer, ...)`, `make_custom_builds_fn(zen_partial=True, populate_full_signature=True)` and `zen(task).hydra_main(...)`. Our pattern is the same.

## 6. Install footprint and CPU vs CUDA wheels with uv

- **PyPI's Linux torch wheels are CUDA builds.** torch 2.15.0 from PyPI depends on `cuda-toolkit==13.2.2` and the `nvidia-*-cu13` wheels (PyPI metadata). uv's guide says PyPI provides GPU-accelerated Linux wheels and macOS/Windows get CPU-only (https://docs.astral.sh/uv/guides/integration/pytorch/).
- CUDA download for torch, torchvision, triton and the nvidia/cuda wheels at the resolved versions is **≈ 3.3 GB** (sum of PyPI wheel sizes, cp312 manylinux x86_64). torch alone is 539 MB, cuDNN 595 MB and cuBLAS 513 MB.
- The CPU wheel `torch-2.15.0+cpu` (https://download.pytorch.org/whl/cpu) is a **210 MB** download and ~750 MB installed. The whole scratch CPU venv (base stack + ml) was 1.5 GB.
- **CUDA 13 needs NVIDIA driver ≥ 580** on Linux (CUDA 13.x minor-version compatibility, NVIDIA CUDA Toolkit release notes, Table 3). torch 2.15.0 exists only as `+cpu`, `+cu132`, `+cu134`, ROCm and XPU on the PyTorch index. **The newest torch with a CUDA 12.6 build is 2.14.1** (`+cu126`, driver ≥ 525 for 12.x). If the hosted runner has an older driver, use 2.14.1 from `https://download.pytorch.org/whl/cu126` (index listing `https://download.pytorch.org/whl/torch/`).
- `--torch-backend` / `UV_TORCH_BACKEND` (`auto`, `cpu`, `cu1xx`) **works only for `uv pip`** and is in preview. It does not affect `uv lock` / `uv sync` (uv settings reference, `torch-backend`; uv PyTorch guide). It is not usable with this repo's `uv sync --frozen` workflow.
- The project-native way is an explicit PyTorch index plus sources scoped to a group or extra, with those groups declared as conflicts. uv's `conflicts` setting accepts `{ group = ... }` entries (uv settings reference). The guide shows the same idea with extras (`{ index = "pytorch-cpu", extra = "cpu" }`). I verified that **group-scoped sources** (`{ index = ..., group = "ml-cpu" }`) lock and sync with uv 0.10.7, both on a toy project and on the scratch copy of this repo:
  - `uv sync --group ml --group ml-cpu` → `torch==2.15.0+cpu`, `torchvision==0.29.2+cpu`, no nvidia wheels.
  - `uv sync --group ml --group ml-cuda` → `torch==2.15.0` from PyPI plus `nvidia-cudnn-cu13` and the rest.
  - `--group ml-cpu --group ml-cuda` → `error: Groups 'ml-cpu' and 'ml-cuda' are incompatible with the conflicts`.
  - `uv.lock` holds both variants (`torch 2.15.0` from PyPI and `2.15.0+cpu` from the CPU index), so `--frozen` works on every machine.

## Recommended `ml` setup (for the implementation issue; not applied here)

```toml
[dependency-groups]
ml = [
    "torch>=2.15.0",
    "torchvision>=0.29.2",
    "lightning>=2.6.6",
    "torchgeo>=0.10.1",
    "segmentation-models-pytorch>=0.6.0",
    "scikit-learn>=1.9.1",
    "hydra-zen[pydantic]>=0.17.2",
    "hydra-core>=1.3.8,<1.4",
]
# Pick exactly one with ml: the dev machine and CPU CI use ml-cpu, the GPU runner uses ml-cuda.
ml-cpu = ["torch>=2.15.0", "torchvision>=0.29.2"]
ml-cuda = ["torch>=2.15.0", "torchvision>=0.29.2"]

[tool.uv]
conflicts = [[{ group = "ml-cpu" }, { group = "ml-cuda" }]]

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cpu", group = "ml-cpu" }]
torchvision = [{ index = "pytorch-cpu", group = "ml-cpu" }]
```

- Resolved set (2026-11-26): torch 2.15.0 (+cpu) · torchvision 0.29.2 (+cpu) · lightning 2.6.6 · torchgeo 0.10.1 · segmentation-models-pytorch 0.6.0 · scikit-learn 1.9.1 · hydra-zen 0.17.2 · hydra-core 1.3.8 · omegaconf 2.3.1 · pydantic 2.14.1 · kornia 0.9.2 · torchmetrics 1.9.0 · timm 1.0.31. numpy stays 2.5.4.
- Install commands: `uv sync --frozen --group ml --group ml-cpu` on the dev machine, and `uv sync --frozen --group ml --group ml-cuda` on the GPU VM. Plain `--group ml` alone falls back to PyPI, which means CUDA on Linux.
- Optional: pin `kornia<0.9` to match TG's tested version. This is only needed if we reuse TG's own `VideoSequential` augmentations, which we should not (§3).
- Optional for TensorBoard logging: `tensorboard` (Lightning's `TensorBoardLogger` needs it, and TG's validation figures need `add_figure`). Otherwise use `CSVLogger`.
- If the GPU runner's driver is < 580: point `ml-cuda` at `https://download.pytorch.org/whl/cu126` with `torch==2.14.1`, `torchvision==0.29.1`, which matches torchvision's `torch>=2.14.0`. Verify that pairing when the runner is chosen.

## Open ambiguities

1. **Binary loss with ignore 255:** `focal`/`dice` (one logit) or `multiclass` + `ce` with 2 classes (two logits, `class_weights`)? This choice changes the model card's `classes`/`positive` and how `predict_proba` reads the output. TG's `bce` + `pos_weight` silently trains on 255 and is ruled out unless the converter maps ignore pixels away.
2. **FC-Siam with three slots** (`pre2+pre1+post`) needs a custom N-branch FCSiamConc. Should the first training runs be restricted to `pre1+post`?
3. **Different mask and predict conventions** between the two TG tasks (`[H,W]` vs `[1,H,W]` masks; dict vs Tensor from `predict_step`). Should the datamodule shape the mask per task, or should a thin task subclass normalise them?
4. **ImageNet vs random init** for 2–3 channel SAR input. smp adapts the RGB first conv. TG's Sentinel-1 pretrained weights exist only for ResNet50. HF Hub access is required on the training VM for `encoder_weights='imagenet'` (smp 0.6), so the weights should be cached or mirrored into the bucket.
5. **GPU runner driver version** (≥ 580 for torch 2.15 / CUDA 13). This belongs to the compute ticket.
6. **Blending window** for stitching (Hann vs linear ramp, overlap size relative to the ResNet18 receptive field). This is a design choice for the inference issue, not a library question.
7. TG 0.10.1 predates smp 0.6.0 and kornia 0.9. The smoke test passes, but TG's CI has not covered this combination. A later TG release may change it.
