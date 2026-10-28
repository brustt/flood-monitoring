"""Run eo-pipe's SieveStep on the Peak Water Mask and compare sizes (issue #5)."""
import sys

sys.path[:] = [p for p in sys.path if "OTB" not in p]

import tempfile
from pathlib import Path

import numpy as np
from osgeo import gdal

from eo_pipe.steps.raster.sieve import SieveStep

gdal.UseExceptions()
TMP = "/home/brustt/.claude/jobs/f0818d2a/tmp/"
db = np.load(TMP + "db.npy")
TH, PEAK = -15.0, 29  # 2026-02-23
mask = (db[PEAK] < TH).astype("uint8")
h, w = mask.shape
work = Path(tempfile.mkdtemp(dir=TMP))
src = work / "mask.tif"
ds = gdal.GetDriverByName("GTiff").Create(str(src), w, h, 1, gdal.GDT_Byte)
ds.GetRasterBand(1).WriteArray(mask)
ds = None
print("raw water px", int(mask.sum()))
for thr in (5, 20, 50, 100):
    for conn in (4, 8):
        out_dir = work / f"o{thr}_{conn}"
        out_dir.mkdir()
        res = SieveStep().execute([src], out_dir, threshold=thr, connectedness=conn)
        out = gdal.Open(str(res.outputs[0].path)).ReadAsArray()
        print(f"threshold={thr} connectedness={conn}: water px {int(out.sum())}")
