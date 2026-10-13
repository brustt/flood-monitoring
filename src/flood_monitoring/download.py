"""Download the Series as Subsets over the Area of Interest."""

from __future__ import annotations

from .config import EventConfig
from .paths import EventPaths


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    raise NotImplementedError("the download stage is not built yet")
