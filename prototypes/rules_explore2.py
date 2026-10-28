"""Baseline / Drop threshold / Event Start rule sweep."""
import numpy as np
import xarray as xr

TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
db = np.load(TMP + "db.npy")
t = xr.open_dataset(D + "filtered-cube.nc").time.values.astype("datetime64[D]")
lab = [str(d)[5:] for d in t]
for n in (8, 12, 16):
    base = np.nanmedian(db[:n], 0)
    for thr in (3, 4, 5):
        sh = np.nanmean((base - db) > thr, axis=(1, 2))
        print(f"N={n} thr={thr}", " ".join(f"{l}:{s*100:.2f}" for l, s in zip(lab[n:], sh[n:])))
    print()
# running-baseline: median of all dates before candidate (>=8 dates)
for thr in (3, 4):
    out = []
    for k in range(8, len(t)):
        b = np.nanmedian(db[:k], 0)
        out.append(f"{lab[k]}:{np.nanmean((b-db[k])>thr)*100:.2f}")
    print("running thr", thr, " ".join(out))
