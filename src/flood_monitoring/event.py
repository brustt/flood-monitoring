"""Detect the Event Start from the VV Drop."""

from __future__ import annotations

from .config import EventConfig
from .paths import EventPaths


def run(config: EventConfig, paths: EventPaths, force: bool = False) -> None:
    raise NotImplementedError("the event stage is not built yet")
