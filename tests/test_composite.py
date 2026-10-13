import numpy as np
import pytest

from flood_monitoring.composite import NODATA, composite_bands, render_rgb

RANGES = {"vv": (-25.0, 0.0), "vh": (-30.0, -5.0), "vv_minus_vh": (0.0, 15.0)}


def test_bands_are_vv_vh_and_their_difference_in_db():
    vv = np.array([[0.1, 0.01]], dtype="float32")  # -10 dB, -20 dB
    vh = np.array([[0.01, 0.001]], dtype="float32")  # -20 dB, -30 dB
    out = composite_bands(vv, vh)
    assert out.shape == (3, 1, 2)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out[0], [[-10, -20]], atol=1e-4)
    np.testing.assert_allclose(out[1], [[-20, -30]], atol=1e-4)
    np.testing.assert_allclose(out[2], [[10, 10]], atol=1e-4)


def test_invalid_pixels_become_declared_nodata_in_every_band():
    vv = np.array([[0.1, -32768.0, 0.1, np.nan]], dtype="float32")
    vh = np.array([[0.1, 0.1, 0.0, 0.1]], dtype="float32")
    out = composite_bands(vv, vh, nodata=-32768.0)
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
