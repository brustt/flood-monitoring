"""Clean GDAL/PROJ environment, applied by the CLI before GDAL is loaded.

Shells that export OTB's ``PROJ_LIB``, ``GDAL_DATA``, ``LD_LIBRARY_PATH``,
``GDAL_DRIVER_PATH`` or ``PYTHONPATH`` make this project's GDAL load OTB's old
``proj.db`` and libraries, and every download fails. ``LD_LIBRARY_PATH`` is read
by the dynamic loader at process start, so setting it in-process is too late:
the CLI re-executes itself once with a clean environment.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Variables that must not leak into the process.
UNSET = ("PYTHONPATH", "LD_LIBRARY_PATH", "GDAL_DRIVER_PATH", "PROJ_LIB")
_FALLBACK_GDAL_DATA = "/usr/local/share/gdal"
# Set in the re-executed process so that it never re-executes again.
REEXECUTED = "FLOOD_MONITORING_CLEAN_ENV"


def _pyproj_data() -> str | None:
    spec = importlib.util.find_spec("pyproj")
    if spec is None or spec.origin is None:
        return None
    path = Path(spec.origin).parent / "proj_dir" / "share" / "proj"
    return str(path) if path.is_dir() else None


def _gdal_data() -> str:
    exe = shutil.which("gdal-config")
    if exe:
        out = subprocess.run([exe, "--datadir"], capture_output=True, text=True)
        if out.returncode == 0 and Path(out.stdout.strip()).is_dir():
            return out.stdout.strip()
    return _FALLBACK_GDAL_DATA


def clean_environment(env: dict[str, str]) -> dict[str, str]:
    """Return ``env`` without the clashing variables and with PROJ/GDAL data set."""
    clean = {k: v for k, v in env.items() if k not in UNSET}
    proj = _pyproj_data()
    if proj:
        clean["PROJ_DATA"] = proj
    clean["GDAL_DATA"] = _gdal_data()
    return clean


def ensure_clean_environment(argv: list[str]) -> None:
    """Re-execute the CLI with a clean environment unless it already has one.

    Re-executes at most once: imports may edit the environment again (eo-pipe's OTB
    package pops ``PROJ_DATA``), so comparing alone would re-execute forever.
    """
    if os.environ.get(REEXECUTED):
        return
    clean = clean_environment(dict(os.environ))
    if clean == dict(os.environ):
        return
    clean[REEXECUTED] = "1"
    os.execve(
        sys.executable,
        [sys.executable, "-m", "flood_monitoring.cli", *argv],
        clean,
    )
