"""Water-mode valley per post-event date, and Otsu variants (issue #5, follow-up)."""
import numpy as np
import xarray as xr
from scipy.ndimage import gaussian_filter1d
from skimage.filters import threshold_otsu

TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
db = np.load(TMP + "db.npy")
t = xr.open_dataset(D + "filtered-cube.nc").time.values.astype("datetime64[D]")
lab = [str(d)[5:] for d in t]
edges = np.arange(-30, 5, 0.25)
ctr = edges[:-1] + 0.125


def valley(v):
    h = gaussian_filter1d(np.histogram(v, edges)[0].astype(float), 2)
    land = np.argmax(h)  # main mode
    lo = np.searchsorted(ctr, -24)
    sub = h[lo:land]
    # water mode = highest local max left of the land mode, at least 3 dB from it
    cand = [i for i in range(1, len(sub) - 1) if sub[i] >= sub[i - 1] and sub[i] > sub[i + 1] and ctr[lo + i] < ctr[land] - 3]
    if not cand:
        return None, ctr[land], None
    w = lo + max(cand, key=lambda i: sub[i])
    vi = w + np.argmin(h[w:land])
    return ctr[vi], ctr[land], ctr[w]


for i in range(lab.index("02-05"), len(lab)):
    v = db[i][np.isfinite(db[i])]
    val, land, wat = valley(v)
    o_all = threshold_otsu(v[::7])
    o_tail = threshold_otsu(v[v < land - 1][::3])  # Otsu on the left half of the land mode
    print(lab[i], f"land mode {land:.1f} water mode {wat} valley {val}",
          f"| otsu all {o_all:.1f} ({(v<o_all).mean()*100:.1f}%)",
          f"| otsu left-of-mode {o_tail:.1f} ({(v<o_tail).mean()*100:.1f}%)",
          f"| -15 dB {(v<-15).mean()*100:.1f}%")
