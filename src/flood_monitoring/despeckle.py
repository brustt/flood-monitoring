"""Reduce speckle in the Cube (Lee, then Quegan) to give the Filtered Cube."""

from __future__ import annotations

from .config import EventConfig
from .paths import EventPaths


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    raise NotImplementedError("the despeckle stage is not built yet")
