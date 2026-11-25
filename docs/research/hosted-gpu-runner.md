# Hosted GPU runner: Coiled vs Modal vs SkyPilot vs Lightning AI

Research for #50 (map #45). Question: which hosted runner should train our models on one GPU?

All pages were read on **2026-11-26**. Prices change: re-check the URLs before relying on a figure. Nothing was
signed up for. Where a claim is my inference rather than something a page states, it says so.

## Answer

**Modal.** It is the only candidate that needs no cloud account, no GPU quota request and no VM image work. A T4 costs
$0.59/h ($0.000164/s) plus CPU and RAM, billed per second, and the Starter plan includes $30 of compute a month. Its
`Image.uv_sync` builds the image from our `pyproject.toml` + `uv.lock`, and it can exclude the `full` group, so
`eo-pipe` (local path) and the git dependencies never reach the image. GPU functions are always preemptible, but Modal
restarts a preempted call on the same input, and resuming from Lightning's `last.ckpt` is the documented way to handle
that.

**SkyPilot** is the fallback, and the better choice if the owner already has AWS/GCP credits or a GPU quota. It is
cheaper per hour (no platform fee, spot T4 from ~$0.09–0.26/h), runs on a plain VM, and works with a real S3/GCS
bucket. It costs more to set up: a cloud account, a GPU quota request (AWS default is 0), a local API server and,
for preemption recovery, a jobs controller VM.

## Comparison

Prices are for one GPU per hour, read on 2026-11-26. "EC2" prices are AWS on-demand for the smallest instance with
that GPU (4 vCPU, 16 GiB). They come from the SkyPilot catalog, which is scraped from the AWS price API (secondary
source, see Sources).

| | Coiled | Modal | SkyPilot | Lightning AI |
|---|---|---|---|---|
| **What it is** | Managed service that starts VMs in *your* AWS/GCP/Azure account | Serverless containers on Modal's own GPU pool | Open-source CLI (Apache-2.0, v0.14.3) that starts VMs in your cloud accounts | Hosted "AI cloud": Studios (dev VMs) and Jobs |
| **T4** | EC2 $0.526 (us-east-1) / $0.615 (eu-west-3) **+ Coiled fee** $0.15 GPU + $0.05/vCPU = $0.35 (pricing page; the GPU page lists $0.45 for g4dn.xlarge) | $0.590 GPU + CPU $0.047/core·h + RAM $0.008/GiB·h | EC2 $0.526 / $0.615; spot $0.21–0.26 (us-east-1), $0.09–0.14 (eu-west-3). GCP T4 $0.35 + n1-standard-4 $0.19 (us-central1) | $0.55–0.88 (several unlabelled grids) |
| **L4** | EC2 g6.xlarge $0.805 / $1.022 + Coiled $0.25 GPU + $0.20 CPU | $0.799 + CPU/RAM | EC2 $0.805 / $1.022; spot $0.56–0.72 / $0.25–0.26 | $0.79–1.25 |
| **A10G / A10** | EC2 g5.xlarge $1.006 / $1.277 + Coiled $0.45 GPU + $0.20 CPU | A10 $1.102 + CPU/RAM | EC2 $1.006 / $1.277; spot $0.83–0.90 / $0.71–0.99 | not listed |
| **Billing** | Coiled fee per second. AWS per second, 60 s minimum | Per second, including image load. Region pinning ×1.15–1.75 | Cloud's own billing (AWS per second, 60 s min). SkyPilot is free | Per second, in credits |
| **Free allowance** | $25/month of Coiled fees (cloud bill extra) | $30/month of compute (Starter) | none (cloud free tiers only) | 30 credits once, expire after 12 months; Pro $50/mo with 40 credits |
| **Cloud account** | AWS, GCP or Azure required; `coiled setup aws` creates IAM roles | none (Modal account only) | at least one: AWS, GCP, Azure, Lambda, RunPod, Nebius, Kubernetes, … | none (own pool). Using your own AWS/GCP (BYOC) is Enterprise only |
| **GPU quota** | yours. AWS "Running On-Demand G and VT instances" default **0** vCPU; `coiled setup aws --quotas` helps request it | none | yours. AWS defaults are **0** for both on-demand and spot G/VT; GCP/Azure quotas by request | none |
| **Environment build** | Package sync copies the *local* env (default), or `--container` image, or a Coiled software env | `Image.uv_sync(groups=…, frozen=True, extra_options=…)` from `pyproject.toml` + `uv.lock`, cached as image layers | `workdir` rsynced to the VM; `setup:` runs any shell, e.g. `uv sync --frozen …` (on every launch unless baked into a Docker image) | Studio environment snapshot (Studios ship `uv`; `uv sync` documented), or a Docker image |
| **Excluding `eo-pipe`** | Package sync would see the editable `eo-pipe` and GDAL bindings in the local venv. Needs `--package-sync-ignore`, a separate local venv, or the container route (inference, untested) | `extra_options="--no-default-groups"` + `groups=["ml"]` (verified with uv, see below) | `uv sync --frozen --no-default-groups --group ml` in `setup:` | same `uv sync` command inside the Studio |
| **Data in** | VM instance profile → S3 (`aws s3 cp` at start). Batch jobs do not forward local credentials by default | Modal Volume (1 TiB free, then $0.09/GiB·mo) or `CloudBucketMount` (S3, R2, GCS); copy to local disk (512 GiB default, up to 3 TiB) | `file_mounts` `mode: COPY` prefetches a bucket prefix to local disk (S3, GCS, R2, Azure, …) | S3/GCS/R2 "data connections" mounted in the Studio; Teamspace Drive |
| **Results out** | `aws s3 sync` from the script; or `--download`/`--sync` dirs | Volume (background commits every few seconds) or bucket mount (no appends, no renames) | `mode: MOUNT_CACHED` (all writes work, async upload) for checkpoints and run dirs | Job outputs in Teamspace Drive; or write to S3 connection |
| **Hydra single run** | `coiled batch run python -m … overrides` | Function receives the override list and calls the hydra-zen app | `run:` calls the app | `lightning job run --command="…"` |
| **Hydra `--multirun`** | No launcher plugin. Sequential sweep in one job, or one job per point | No launcher plugin. Sequential in one call, or `.map` over sweep points (one container each) | No launcher plugin. Sequential in one task, or one `sky jobs launch` per point | No launcher plugin. Sequential, or one job per point |
| **Spot / preemption** | `--spot-policy spot\|spot_with_fallback` (workers only; the scheduler VM is always on-demand). Replacing reclaimed spot VMs is documented for AWS only; batch `--max-retries` covers task failures | GPU functions **always** preemptible (`nonpreemptible` not supported for GPU). Preempted calls restart on the same input; `modal.Retries` for failures; 24 h max per attempt | `use_spot: true`. Managed jobs (`sky jobs launch`) recover from preemption on a new VM, retry capacity shortages indefinitely; `max_restarts_on_errors` for code failures | `--interruptible` (paid plans only), 2-minute warning, "start saving". Automatic job restart not documented |
| **Setup effort** | Medium: Coiled account + AWS account + IAM setup + quota | **Low**: account + `modal setup` + one Python file | Medium–high: cloud account + quota + `sky check` + YAML | Low–medium: account + phone check + Studio setup; interruptible needs a paid plan |

### Local path dependency: what uv does without `../eo-pipe`

`eo-pipe`, `eo-stac` and `platform-publish` live only in the `full` dependency group. `full` and `dev` are
`default-groups`, so a plain `uv sync` installs them. Tested on 2026-11-26 with uv 0.10.7, on a copy of `pyproject.toml`
+ `uv.lock` in a folder with no `../eo-pipe` next to it:

| Command | Result |
|---|---|
| `uv sync --frozen --no-default-groups --dry-run` | works: installs the light set only, no eo-pipe, no git fetches |
| `uv sync --frozen --dry-run` | fails: `Distribution not found at: file:///…/eo-pipe` |
| `uv lock --check` | fails: `Failed to generate package metadata for eo-pipe … editable+../eo-pipe` |

So a remote build must be **frozen** (no re-locking) and pass **`--no-default-groups`**, plus `--group ml` once the
`ml` group exists. The lock must be refreshed on the dev machine, where `../eo-pipe` exists. Modal's `uv_sync`
defaults to `frozen=True` but its `groups` argument only *adds* `--group`, so `--no-default-groups` must go in
`extra_options`. `uv_sync` does not install the project itself; the `flood_monitoring` source is added with
`add_local_python_source("flood_monitoring")`. That is fine because `flood_monitoring/__init__.py` imports nothing
(checked). The `ml` subpackage must also never import eo-pipe, eo-stac or platform-publish.

The `[tool.uv.extra-build-*]` GDAL settings only matter when `gdal` is built, which happens only with `full`.

PyPI's Linux `torch` wheels bundle CUDA, so a CPU-only lock still gives a GPU-capable torch on every runner. This is
what the default PyPI wheel does; whether the `ml` group pins a specific CUDA index is up to the training issue.

## Recommendation: Modal

Why, for this project (one owner, no GPU at home, one GPU per run, small budget):

- **Least setup.** No cloud account, IAM or quota ticket. AWS GPU quotas start at 0, and SkyPilot's quota page warns
  approvals "usually take a few days". The setup task (#56) becomes an afternoon.
- **The environment comes from the lock file.** `uv_sync` reads `pyproject.toml` + `uv.lock` and caches the layer,
  so the training env is exactly the `ml` group, built once per lock change. SkyPilot re-runs `uv sync` on every
  launch, and Coiled's default package sync copies the messy local venv (editable eo-pipe, GDAL bound to
  `/usr/local`).
- **The cost fits the free allowance.** $30/month covers roughly 35 h of T4 with 2 cores and 16 GiB
  (≈ $0.81/h), billed per second, with no idle VM to forget.
- **Preemption is handled the same way we would have to anyway.** Modal restarts the same input after a preemption,
  so the training entry point resumes from `last.ckpt` in the run folder (Modal's own long-training example does this
  with Lightning, a Volume and `modal.Retries`).

Costs we accept:

- **Lock-in.** The launch file and Volume are Modal-specific, but the training app itself stays a plain hydra-zen CLI.
  Keep Modal code to one thin module (`flood_monitoring/ml/remote_modal.py` or a top-level `modal_app.py`) so SkyPilot
  can replace it.
- **Per-hour price.** About 1.5× an AWS on-demand T4 and 3–6× AWS spot. Revisit if monthly spend regularly passes
  ~$50, or if runs get longer than one 24 h attempt.
- **The "bucket" is a Modal Volume** unless we add R2 (see open questions). Moving data out is `modal volume get`;
  it is free up to 1 TiB/month of egress on Starter (pricing page).

### Setup steps (checklist for #56)

1. **Account.** Create a Modal account (Starter plan, $0 + compute). Set a workspace spend limit in the dashboard
   (the pricing FAQ points to spend limits; check the exact setting there).
2. **CLI and token.** Install the client outside the project env, e.g. `uv tool install modal`, then `modal setup`.
   The token is written to `~/.modal.toml`; record that path in the setup notes, never the value.
3. **Volume.** `modal volume create --version=2 flood-ml` (v2 is recommended but is *not* the default). Layout:
   `datasets/<name>/<version>/shard-*.tar`, `runs/<run-id>/` (Hydra run folder, checkpoints, CSV/TensorBoard logs),
   `models/<card-name>/`.
4. **Check the env build locally.** Once the `ml` group exists: `uv lock`, then in a scratch copy of the repo without
   `../eo-pipe` run `uv sync --frozen --no-default-groups --group ml --dry-run`. It must succeed.
5. **Smoke app.** A single file with:
   - `image = modal.Image.debian_slim(python_version="3.12").uv_sync(groups=["ml"], extra_options="--no-default-groups").add_local_python_source("flood_monitoring")`
     (pin `uv_version` to the local uv, 0.10.7)
   - `@app.function(gpu="T4", image=image, volumes={"/vol": modal.Volume.from_name("flood-ml")}, timeout=3600, retries=modal.Retries(max_retries=3, initial_delay=0.0))`
   - The body prints `torch.cuda.is_available()` and `nvidia-smi`, copies one small tar shard from
     `/vol/datasets/…` to local disk and untars it, writes `/vol/runs/smoke-<id>/ok.txt`, then calls `vol.commit()`.
6. **Upload a test shard.** `modal volume put flood-ml smoke.tar datasets/smoke/v0/shard-000.tar`.
7. **Run it.** `modal run <file>::smoke`, then check the result with `modal volume ls flood-ml runs/` and
   `modal volume get`.
8. **Record** in the setup notes: token location, Volume name and layout, GPU type (T4; L4 as the step up), region
   (unpinned, since pinning costs 1.15–1.75×), and the smoke run's cost from the dashboard's usage page.

Design points for the workflow ticket (#57), found here:

- **Pick the run id locally**, then pass it as `hydra.run.dir=/vol/runs/<id>`. If the folder name used `${now:…}`, a
  restarted (preempted) call would start a new folder instead of resuming.
- **Sweeps.** No Hydra launcher plugin exists for Modal, Coiled, SkyPilot or Lightning. The official launchers are
  Joblib, Ray (`ray_aws` is "best-effort" maintained), RQ and Submitit (SLURM/local). To keep the map's "one GPU VM per
  run", expand the sweep locally and call the function once per point (`.map`/`.spawn`). Running `--multirun` inside
  one call also works, but it shares one 24 h attempt and one preemption.
- **Bucket mount caveat.** If R2/S3 is mounted with `CloudBucketMount`, it cannot append or rename files, so Lightning
  CSV/TensorBoard logs and checkpoints should not be written there directly. Write them to the Volume or local disk,
  then copy.

## Why not the others

- **Coiled.** It is a Dask-first product. GPU training works through `coiled run`/`coiled batch run`, but it needs your
  own cloud account and quota *and* adds a fee on top (T4 total ≈ $0.88–1.07/h). Its default package sync mirrors the
  local venv, which here contains an editable eo-pipe and GDAL bindings compiled against `/usr/local`. Spot replacement
  is documented for AWS only and not for batch jobs. A one-task batch job also starts an on-demand scheduler VM unless
  the task runs on it (`--task-on-scheduler`).
- **SkyPilot.** Technically the best fit for "a VM plus a bucket": `COPY` for shards, `MOUNT_CACHED` for run folders,
  managed-job preemption recovery. It is also the cheapest per hour. It loses on setup effort: an AWS/GCP account,
  quota requests starting from 0, and a local API server. Recovery needs a jobs controller VM (~$0.25/h while running,
  autostops after 10 min idle) when the API server runs locally. It is the natural fallback, and the one to pick if
  cloud credits appear.
- **Lightning AI.** Its model is Studio-centric: develop in a cloud Studio, then submit its snapshot as a Job. That
  sits awkwardly with "develop locally, launch remotely". Its free tier is a one-off 30 credits, interruptible machines
  need a paid plan, using your own cloud is Enterprise only, automatic restart of interrupted jobs is not documented,
  and the pricing page shows several unlabelled price grids for the same GPU.

## Open questions

- **Bucket: Modal Volume or R2?** The map says datasets live "in a bucket". With Modal, the simplest is a Volume (1 TiB
  free). A Cloudflare R2 bucket ($0.015/GB·mo, no egress fees, 10 GB free) mounted read-only would keep datasets
  provider-neutral, but it is a second account. S3 would add AWS egress fees to Modal. Owner to choose in #56.
- **Does the owner have AWS/GCP credits or an account with GPU quota?** If yes, SkyPilot's lower hourly cost and plain
  bucket may outweigh Modal's simpler setup.
- **Dataset size.** Copying shards to local disk is fine up to Modal's 512 GiB default disk (3 TiB max). Very large
  KuroSiwo subsets would favour streaming instead. This depends on #48.
- **Run length.** Modal caps one attempt at 24 h, and retries reset the timer. Runs expected to exceed that need
  epoch-level resume (planned anyway) and enough `max_retries` (up to 10).
- **CPU and RAM for data loading.** Modal bills CPU and RAM on top of the GPU. The core count needed to keep a T4 fed
  with GeoTIFF patches is unknown until a first run; it changes the hourly cost by ~$0.05 per core.
- **Coiled's own figures disagree**: $0.15 T4 + $0.05/vCPU (pricing page) vs $0.45/h for g4dn.xlarge (GPU page). Not
  resolved, since Coiled is not recommended.
- **Spend cap on Modal.** The pricing FAQ links to "spend limits"; the setup task should confirm that this covers
  compute and not only Model APIs.

## Sources (read 2026-11-26)

- Coiled pricing: https://www.coiled.io/pricing
- Coiled Batch (flags, spot policy, data access, GPU example): https://docs.coiled.io/user_guide/batch.html
- Coiled `coiled run`: https://docs.coiled.io/user_guide/cli-jobs.html
- Coiled GPU (per-instance Coiled prices, CPU↔GPU torch translation): https://docs.coiled.io/user_guide/clusters/gpu.html
- Coiled package sync: https://docs.coiled.io/user_guide/software/sync.html and https://docs.coiled.io/user_guide/software/package-sync-advanced.html
- Coiled spot: https://docs.coiled.io/user_guide/costs/spot.html
- Coiled remote data access: https://docs.coiled.io/user_guide/data/remote-data-access.html
- Coiled AWS setup (`--quotas`): https://docs.coiled.io/user_guide/setup/aws/cli.html
- Coiled + uv container: https://docs.coiled.io/blog/uv-coiled-cloud-scripts.html
- Modal pricing: https://modal.com/pricing
- Modal `Image.uv_sync`: https://modal.com/docs/reference/modal.Image.md
- Modal images guide: https://modal.com/docs/guide/images
- Modal preemption: https://modal.com/docs/guide/preemption
- Modal timeouts: https://modal.com/docs/guide/timeouts
- Modal Volumes: https://modal.com/docs/guide/volumes
- Modal cloud bucket mounts: https://modal.com/docs/guide/cloud-bucket-mounts
- Modal disk limits: https://modal.com/docs/guide/resources
- Modal GPU types: https://modal.com/docs/guide/gpu
- Modal long-training example: https://modal.com/docs/examples/long-training
- SkyPilot install and clouds: https://docs.skypilot.ai/en/latest/getting-started/installation.html
- SkyPilot managed jobs: https://docs.skypilot.ai/en/latest/examples/managed-jobs.html
- SkyPilot storage modes: https://docs.skypilot.ai/en/latest/reference/storage.html
- SkyPilot YAML spec: https://docs.skypilot.ai/en/latest/reference/yaml-spec.html
- SkyPilot autostop: https://docs.skypilot.ai/en/latest/reference/auto-stop.html
- SkyPilot quota: https://docs.skypilot.ai/en/latest/cloud-setup/quota.html
- SkyPilot licence and release: https://github.com/skypilot-org/skypilot (Apache-2.0, v0.14.3 of 2026-11-16)
- EC2 prices (secondary: SkyPilot catalog, scraped from the AWS/GCP price APIs): https://github.com/skypilot-org/skypilot-catalog/tree/master/catalogs/v8 (`aws/vms.csv`, `gcp/vms.csv`)
- AWS per-second billing: https://aws.amazon.com/ec2/pricing/on-demand/
- AWS default quotas: https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html
- Lightning AI pricing: https://lightning.ai/pricing
- Lightning AI docs (llms index): https://lightning.ai/llms.txt; pages read as `.md`: `platform/build/ai-studio/interruptible-machines`, `platform/developers/cli/batch`, `platform/inference/batch-jobs`, `platform/build/ai-studio/modify-environment`, `platform/build/ai-studio/environment-persistence`, `platform/data/data-connections/s3-buckets`, `platform/gpu-cloud/overview` (under https://lightning.ai/docs/)
- Hydra launchers: https://hydra.cc/docs/plugins/submitit_launcher/ and https://hydra.cc/docs/plugins/ray_launcher/
- Cloudflare R2 pricing: https://developers.cloudflare.com/r2/pricing/
