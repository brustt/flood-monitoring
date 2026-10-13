"""Water Masks of the Post-event Acquisitions, then the Flood Extent."""

from __future__ import annotations

from .config import EventConfig
from .paths import EventPaths


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    raise NotImplementedError("the flood-extent stage is not built yet")
