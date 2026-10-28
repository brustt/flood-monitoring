"""Write the reference outputs of the synthetic inputs (tests/reference_inputs.py) to tests/data/reference/.

Run on the code before the xarray/dask rewrite: ``uv run python tests/data/make_reference.py``.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
import reference_inputs as ri  # noqa: E402

from flood_monitoring import composite, event, flood_extent, merge_stage  # noqa: E402

REFERENCE = HERE / "reference"


def save(out: Path, processed: Path, summary: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    arrays, meta = ri.read_outputs(processed)
    np.savez_compressed(out / "arrays.npz", **arrays)
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.copy(summary, out / "summary.json")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, paths = ri.single_series(Path(tmp) / "single")
        for stage in (event, flood_extent, composite):
            stage.run(config, paths, force=True)
        save(REFERENCE / "single", paths.processed, paths.summary)

        config, paths = ri.two_series(Path(tmp) / "merge")
        merge_stage.run(config, paths, force=True)
        save(REFERENCE / "merge", paths.processed, paths.summary)
    print(f"reference written to {REFERENCE}")


if __name__ == "__main__":
    main()
