"""Write the Layers as COGs, the Publish manifests, validate and archive."""

from __future__ import annotations

from .config import EventConfig
from .paths import EventPaths


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    raise NotImplementedError("the export stage is not built yet")
