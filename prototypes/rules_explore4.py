"""Fixed-threshold water, Permanent Water, Peak; writes maps for visual check."""
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from skimage.filters import threshold_otsu

TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
db = np.load(TMP + "db.npy")
t = xr.open_dataset(D + "filtered-cube.nc").time.values.astype("datetime64[D]")
lab = [str(d)[5:] for d in t]
ev = lab.index("02-05")
for th in (-14, -15, -16, -17, -18):
    w = db < th
    print(th, "pre median water%", round(float(np.median(w[:ev].mean((1, 2))) * 100), 2),
          "post:", " ".join(f"{l}:{w[i].mean()*100:.2f}" for i, l in zip(range(ev, len(lab)), lab[ev:])))
# Otsu on the low tail only (pixels below -10 dB) as a data-driven alternative
v = db[ev:][db[ev:] < -10]
print("otsu on tail <-10:", threshold_otsu(v[::50]))

fig, ax = plt.subplots(2, 3, figsize=(21, 10))
for a, l in zip(ax.ravel(), ["01-30", "02-05", "02-11", "02-17", "02-23", "03-01"]):
    a.imshow(db[lab.index(l)], cmap="gray", vmin=-22, vmax=0)
    a.set_title(f"VV dB {l}"); a.axis("off")
plt.tight_layout(); plt.savefig(TMP + "vv.png", dpi=60)
