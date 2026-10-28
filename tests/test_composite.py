import numpy as np
import pytest
import xarray as xr

from flood_monitoring.composite import NODATA, composite_bands, render_rgb

RANGES = {"vv": (-25.0, 0.0), "vh": (-30.0, -5.0), "vv_minus_vh": (0.0, 15.0)}


def yx(values):
    return xr.DataArray(np.array(values, dtype="float32"), dims=("y", "x")).chunk()


def test_bands_are_vv_vh_and_their_difference_in_db():
    vv = yx([[0.1, 0.01]])  # -10 dB, -20 dB
    vh = yx([[0.01, 0.001]])  # -20 dB, -30 dB
    out = composite_bands(vv, vh)
    assert out.dims == ("band", "y", "x") and out.shape == (3, 1, 2)
    assert out["band"].values.tolist() == ["vv", "vh", "vv_minus_vh"]
    out = out.values
    assert out.dtype == np.float32
    np.testing.assert_allclose(out[0], [[-10, -20]], atol=1e-4)
    np.testing.assert_allclose(out[1], [[-20, -30]], atol=1e-4)
    np.testing.assert_allclose(out[2], [[10, 10]], atol=1e-4)


def test_invalid_pixels_become_declared_nodata_in_every_band():
    vv = yx([[0.1, -32768.0, 0.1, np.nan]])
    vh = yx([[0.1, 0.1, 0.0, 0.1]])
    out = composite_bands(vv, vh, nodata=-32768.0).values
    assert np.all(out[:, 0, 1:] == NODATA)
    assert np.all(out[:, 0, 0] != NODATA)


def test_render_maps_range_to_0_255_and_clips():
    comp = np.array([[[-25.0, 0.0, -50.0]], [[-30.0, -5.0, 0.0]], [[0.0, 15.0, 7.5]]], dtype="float32")
    rgb = render_rgb(comp, RANGES)
    assert rgb.shape == (1, 3, 3) and rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [0, 0, 0]
    assert rgb[0, 1].tolist() == [255, 255, 255]
    assert rgb[0, 2].tolist() == [0, 255, 128]


def test_render_nodata_is_black():
    comp = np.full((3, 1, 1), NODATA, dtype="float32")
    assert render_rgb(comp, RANGES)[0, 0].tolist() == [0, 0, 0]


def test_render_rejects_empty_range():
    with pytest.raises(ValueError, match="vv"):
        render_rgb(np.zeros((3, 1, 1), "float32"), {**RANGES, "vv": (1.0, 1.0)})


def test_killed_forced_run_removes_both_composites(tmp_path, monkeypatch):
    import json

    import reference_inputs as ri
    from flood_monitoring import composite, raster

    config, paths = ri.single_series(tmp_path)
    paths.processed.mkdir(parents=True)
    paths.summary.write_text(json.dumps({"before": "2026-01-12", "peak": "2026-01-13"}))
    composite.run(config, paths)
    assert paths.before_composite.exists() and paths.after_composite.exists()

    def killed(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(raster, "write_partial", killed)
    with pytest.raises(KeyboardInterrupt):
        composite.run(config, paths, force=True)
    assert not paths.before_composite.exists() and not paths.after_composite.exists()
