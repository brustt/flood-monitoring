"""Fixed -15 dB vs Otsu-on-left-tail masks on 02-05 and 02-23 (issue #5)."""
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from skimage.filters import threshold_otsu
from scipy.ndimage import gaussian_filter1d

TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
db = np.load(TMP + "db.npy")
t = xr.open_dataset(D + "filtered-cube.nc").time.values.astype("datetime64[D]")
lab = [str(d)[5:] for d in t]
edges = np.arange(-30, 5, 0.25)
ctr = edges[:-1] + 0.125


def otsu_left(img):
    v = img[np.isfinite(img)]
    mode = ctr[np.argmax(gaussian_filter1d(np.histogram(v, edges)[0].astype(float), 2))]
    return threshold_otsu(v[v < mode - 1][::3])


fig, ax = plt.subplots(2, 2, figsize=(20, 16))
for r, l in enumerate(["02-05", "02-23"]):
    img = db[lab.index(l)]
    th = otsu_left(img)
    a, b = img < -15, img < th
    only_b = b & ~a
    print(l, f"otsu-left {th:.1f} dB: fixed {a.mean()*100:.2f}% otsu {b.mean()*100:.2f}% extra {only_b.mean()*100:.2f}%",
          "| extra px mean VV", round(float(img[only_b].mean()), 1))
    ax[r, 0].imshow(img, cmap="gray", vmin=-22, vmax=0)
    ax[r, 0].set_title(f"VV {l}")
    rgb = np.dstack([np.full(img.shape, 0.9)] * 3)
    rgb[a] = [0, 0.3, 1]          # both
    rgb[only_b] = [1, 0.2, 0.2]   # only Otsu-left (below land mode)
    ax[r, 1].imshow(rgb)
    ax[r, 1].set_title(f"{l}: blue = VV<-15 ; red = extra with Otsu-left ({th:.1f} dB)")
for x in ax.ravel():
    x.axis("off")
plt.tight_layout()
plt.savefig(TMP + "compare.png", dpi=50)
