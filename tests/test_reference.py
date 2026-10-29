"""The rewritten stages give the outputs of the code before the rewrite (tests/data/reference), for any chunk side."""

import json
from pathlib import Path

import numpy as np
import pytest
from eo_pipe.io.memory import ChunkPlan
from rasterio.crs import CRS

import reference_inputs as ri
from flood_monitoring import chunks, composite, event, flood_extent, merge_stage
from flood_monitoring.composite import NODATA, SCALE, STORED_NODATA

REFERENCE = Path(__file__).parent / "data" / "reference"
# The reference was made with float32 dB Composites; they are now int16 hundredths of a dB.
INT16_COMPOSITES = {"before-composite.tif", "after-composite.tif"}
PUBLISHED = {"flood-extent.tif", "before-composite.tif", "after-composite.tif", "orbit-map.tif"}


@pytest.fixture
def plan_calls(monkeypatch):
    calls = []

    def fixed(side):
        def plan(budget_mb, bytes_per_pixel, height, width, stored=chunks.TILE):
            calls.append((budget_mb, bytes_per_pixel, height, width))
            return ChunkPlan(side, 2)

        monkeypatch.setattr(chunks, "plan", plan)
        return calls

    return fixed


def assert_matches(reference: Path, processed: Path, summary: Path) -> None:
    assert json.loads(summary.read_text()) == json.loads((reference / "summary.json").read_text())
    arrays, meta = ri.read_outputs(processed)
    expected_meta = json.loads((reference / "meta.json").read_text())
    with np.load(reference / "arrays.npz") as expected:
        assert sorted(arrays) == sorted(expected.files)
        for key, data in arrays.items():
            if key in INT16_COMPOSITES:
                assert data.dtype == np.int16, key
                nodata = data == STORED_NODATA
                assert np.array_equal(nodata, expected[key] == NODATA), key
                np.testing.assert_allclose(data[~nodata] * SCALE, expected[key][~nodata], atol=SCALE / 2 + 1e-6)
            else:
                assert np.array_equal(data, expected[key]), key
    for key, m in meta.items():
        e = expected_meta[key]
        assert CRS.from_wkt(m["crs"]) == CRS.from_wkt(e["crs"]), key
        if key in INT16_COMPOSITES:
            e = {**e, "dtype": "int16", "nodata": STORED_NODATA}
        assert (m["transform"], m["dtype"], m["nodata"], m["count"]) == (
            e["transform"], e["dtype"], e["nodata"], e["count"],
        ), key
        if key in PUBLISHED:
            assert m["tags"] == e["tags"], key


@pytest.mark.parametrize("side", [16, 7, 1000])
def test_single_series_matches_reference(tmp_path, plan_calls, side):
    calls = plan_calls(side)
    config, paths = ri.single_series(tmp_path)
    for stage in (event, flood_extent, composite):
        stage.run(config, paths, force=True)
    assert_matches(REFERENCE / "single", paths.processed, paths.summary)
    assert calls


@pytest.mark.parametrize("side", [16, 1000])
def test_merge_matches_reference(tmp_path, plan_calls, side):
    calls = plan_calls(side)
    config, paths = ri.two_series(tmp_path)
    merge_stage.run(config, paths, force=True)
    assert_matches(REFERENCE / "merge", paths.processed, paths.summary)
    assert calls
