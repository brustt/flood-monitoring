"""Full rule chain with candidate parameters; maps for visual check."""
import sys
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import ndimage

TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
db = np.load(TMP + "db.npy")
t = xr.open_dataset(D + "filtered-cube.nc").time.values.astype("datetime64[D]")
lab = [str(d)[5:] for d in t]
TH = float(sys.argv[1]) if len(sys.argv) > 1 else -15.0
SIEVE = int(sys.argv[2]) if len(sys.argv) > 2 else 20


def sieve(mask, size, conn=8):
    """Emulates GDAL sieve: drop fg regions and fill bg holes smaller than *size* px."""
    st = np.ones((3, 3)) if conn == 8 else None
    out = mask.copy()
    for val in (True, False):
        lbl, n = ndimage.label(out == val, structure=st)
        sizes = ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))
        small = np.isin(lbl, 1 + np.flatnonzero(np.asarray(sizes) < size))
        out[small] = not val
    return out


# Event Start: baseline = median of all earlier dates (>=8), Drop > 4 dB, share > 1%
ev = None
for k in range(8, len(t)):
    share = np.nanmean((np.nanmedian(db[:k], 0) - db[k]) > 4)
    if share > 0.01:
        ev = k
        break
print("Event Start", lab[ev], "Before", lab[ev - 1])

water = [sieve(db[i] < TH, SIEVE) for i in range(len(t))]
pre_w = np.mean([db[i] < TH for i in range(ev)], 0)  # per-date raw frequency
for frac in (0.25, 0.5):
    print("permanent water frac>", frac, "area%", round(float((pre_w > frac).mean() * 100), 2))
perm = sieve(pre_w > 0.5, SIEVE)
post = list(range(ev, len(t)))
areas = {lab[i]: float((water[i] & ~perm).mean() * 100) for i in post}
print("flooded area% per post date (water & ~perm):", {k: round(v, 2) for k, v in areas.items()})
print("total water% per post date:", {lab[i]: round(float(water[i].mean() * 100), 2) for i in post})
peak = max(areas, key=areas.get)
print("Peak", peak)
extent = np.any([water[i] for i in post], 0) & ~perm
print("Flood Extent area% ", round(float(extent.mean() * 100), 2), " km2", round(float(extent.sum()) * 100 / 1e6, 1))

fig, ax = plt.subplots(1, 3, figsize=(24, 8))
ax[0].imshow(db[ev - 1], cmap="gray", vmin=-22, vmax=0); ax[0].set_title(f"Before {lab[ev-1]}")
rgb = np.zeros(perm.shape + (3,), "float32")
rgb[..., :] = 0.85
rgb[extent] = [1, 0.2, 0.2]
rgb[perm] = [0.1, 0.3, 1]
ax[1].imshow(rgb); ax[1].set_title(f"Flood Extent (red) / Permanent Water (blue) th={TH} sieve={SIEVE}")
ax[2].imshow(db[lab.index(peak)], cmap="gray", vmin=-22, vmax=0)
ax[2].contour(water[lab.index(peak)], levels=[0.5], colors="cyan", linewidths=0.5); ax[2].set_title(f"Peak {peak} + Water Mask")
for a in ax: a.axis("off")
plt.tight_layout(); plt.savefig(TMP + f"chain_{TH}_{SIEVE}.png", dpi=55)
