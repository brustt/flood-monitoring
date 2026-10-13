"""Where each stage reads and writes, per Event."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT

DATA_ROOT_ENV = "FLOOD_MONITORING_DATA_ROOT"
DEFAULT_DATA_ROOT = Path.home() / "Documents" / "Projects" / "data" / "flood-monitoring"


def data_root() -> Path:
    return Path(os.environ.get(DATA_ROOT_ENV, DEFAULT_DATA_ROOT)).expanduser()


@dataclass(frozen=True)
class EventPaths:
    """Folders of one Event. Nothing is created until :meth:`ensure` is called."""

    event: str
    data_root: Path
    repo_root: Path = REPO_ROOT

    @property
    def raw(self) -> Path:
        """Downloaded Subsets of the Series."""
        return self.data_root / "raw" / self.event

    @property
    def interim(self) -> Path:
        """The Cube and the Filtered Cube."""
        return self.data_root / "interim" / self.event

    @property
    def cube(self) -> Path:
        """The Cube: all Acquisitions stacked, VV and VH, linear power."""
        return self.interim / "cube.nc"

    @property
    def filtered_cube(self) -> Path:
        """The Cube after Lee then Quegan."""
        return self.interim / "filtered-cube.nc"

    @property
    def processed(self) -> Path:
        """Event Start, Water Masks, Flood Extent and Composites before export."""
        return self.data_root / "processed" / self.event

    @property
    def summary(self) -> Path:
        """Event Start, Peak, thresholds and areas, as JSON."""
        return self.processed / "summary.json"

    @property
    def water_masks(self) -> Path:
        """One sieved Water Mask GeoTIFF per Post-event Acquisition, ``<yyyy-mm-dd>.tif``."""
        return self.processed / "water-masks"

    @property
    def permanent_water(self) -> Path:
        return self.processed / "permanent-water.tif"

    @property
    def flood_extent(self) -> Path:
        return self.processed / "flood-extent.tif"

    @property
    def before_composite(self) -> Path:
        """Composite of the last Acquisition before the Event Start."""
        return self.processed / "before-composite.tif"

    @property
    def after_composite(self) -> Path:
        """Composite of the Peak."""
        return self.processed / "after-composite.tif"

    @property
    def outputs(self) -> Path:
        """Final Layers, in the repo and git-ignored."""
        return self.repo_root / "outputs" / self.event

    @property
    def publish(self) -> Path:
        """Publish manifests."""
        return self.repo_root / "publish"

    def archive(self, executed_at: datetime) -> Path:
        """Copy of the final Layers kept for the run started at *executed_at*."""
        return self.data_root / "outputs" / executed_at.strftime("%Y%m%dT%H%M%S") / self.event

    def ensure(self) -> None:
        for folder in (self.raw, self.interim, self.processed, self.outputs, self.publish):
            folder.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict[str, Path]:
        return {
            "raw": self.raw,
            "interim": self.interim,
            "processed": self.processed,
            "outputs": self.outputs,
            "publish": self.publish,
            "archive": self.data_root / "outputs" / "<execution_datetime>" / self.event,
        }


def event_paths(event: str) -> EventPaths:
    return EventPaths(event=event, data_root=data_root())
