"""Otsu / Permanent Water / Peak exploration."""
import numpy as np
import xarray as xr
from skimage.filters import threshold_otsu

TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
db = np.load(TMP + "db.npy")
t = xr.open_dataset(D + "filtered-cube.nc").time.values.astype("datetime64[D]")
lab = [str(d)[5:] for d in t]
ev = lab.index("02-05")
pre, post = db[:ev], db[ev:]

def sep(v, th):
    lo, hi = v[v <= th], v[v > th]
    w0, w1 = lo.size / v.size, hi.size / v.size
    var_b = w0 * w1 * (lo.mean() - hi.mean()) ** 2
    return var_b / v.var()  # 1 = perfectly bimodal, ~0.6 for gaussian-ish

rng = np.random.default_rng(0)
allpost = post[np.isfinite(post)]
samp = rng.choice(allpost, 2_000_000, replace=False)
tp = threshold_otsu(samp)
print("pooled post otsu", round(float(tp), 2), "sep", round(float(sep(samp, tp)), 3))
print("pooled pre otsu", round(float(threshold_otsu(rng.choice(pre[np.isfinite(pre)], 2_000_000))), 2))
for i, l in enumerate(lab):
    v = db[i][np.isfinite(db[i])]
    v = rng.choice(v, 500_000, replace=False)
    th = threshold_otsu(v)
    print(l, "otsu", round(float(th), 2), "sep", round(float(sep(v, th)), 3), "water%", round(float((db[i] < th).mean() * 100), 2),
          "water%@pooled", round(float((db[i] < tp).mean() * 100), 2))
h, e = np.histogram(samp, bins=np.arange(-30, 5, 1))
print("hist post", dict(zip(e[:-1].astype(int), (h / h.sum() * 100).round(1))))
