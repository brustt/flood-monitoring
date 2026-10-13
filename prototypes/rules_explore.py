"""Throwaway exploration for issue #5: rules on the real Filtered Cube."""
import numpy as np
import xarray as xr

D = "/home/brustt/Documents/Projects/data/flood-monitoring/interim/saintes-2026-02/"
TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
ds = xr.open_dataset(D + "filtered-cube.nc")
vv = ds.VV.values.astype("float32")
t = ds.time.values.astype("datetime64[D]")
nod = ds.attrs["nodata"]
valid = np.isfinite(vv) & (vv != nod) & (vv > 0)
db = np.where(valid, 10 * np.log10(np.where(valid, vv, 1)), np.nan).astype("float32")
np.save(TMP + "db.npy", db)
print("valid frac per date", valid.mean((1, 2)).round(3))
print("dB pct", np.nanpercentile(db, [1, 5, 25, 50, 75, 95, 99]).round(1))
base = np.nanmedian(db[:20], 0)  # dates up to 2025-12-19
for thr in (2, 3, 4, 5):
    sh = np.nanmean((base - db) > thr, axis=(1, 2))
    print(thr, " ".join(f"{str(d)[5:]}:{s:.3f}" for d, s in zip(t, sh)))
