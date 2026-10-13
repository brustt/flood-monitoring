"""Event config: the one file that says what an Event is and which Series it uses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTS_DIR = REPO_ROOT / "events"


@dataclass(frozen=True)
class Window:
    """Calendar period of the flood, as shown on the Platform."""

    start: date
    end: date


@dataclass(frozen=True)
class Series:
    """Search criteria selecting the Acquisitions of the Event."""

    collection: str
    start: date
    end: date
    orbit_state: str
    instrument_mode: str
    relative_orbit: int
    assets: tuple[str, ...]


@dataclass(frozen=True)
class Despeckle:
    """Lee (spatial) and Quegan (temporal) filter settings; eo-pipe's defaults."""

    lee_window: int = 7
    quegan_window: int = 7
    enl: float = 4.4


@dataclass(frozen=True)
class Detection:
    """Detection rules; the values settled in docs/detection-rules.md."""

    min_baseline: int = 8  # earlier Acquisitions needed before a candidate Event Start
    drop_db: float = 4.0  # a pixel is changed when its Drop exceeds this
    event_share: float = 0.01  # Event Start: first date with more changed pixels than this
    water_db: float = -15.0  # Water Mask: VV below this
    permanent_fraction: float = 0.5  # Permanent Water: water in more than this share of pre-event dates
    sieve_size: int = 20  # eo-pipe sieve: minimum region size, pixels
    sieve_connectedness: int = 8


@dataclass(frozen=True)
class EventConfig:
    project: str
    event: str
    area_of_interest: tuple[float, float, float, float]
    window: Window
    series: Series
    despeckle: Despeckle = Despeckle()
    detection: Detection = Detection()


def load_event_config(event: str, events_dir: Path = EVENTS_DIR) -> EventConfig:
    """Load the config of *event* from ``<events_dir>/<event>.yaml``."""
    path = events_dir / f"{event}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in events_dir.glob("*.yaml"))
        raise FileNotFoundError(f"no config for Event {event!r} in {events_dir} (known: {known})")
    raw = yaml.safe_load(path.read_text())
    if raw["event"] != event:
        raise ValueError(f"{path} declares Event {raw['event']!r}, expected {event!r}")

    west, south, east, north = (float(v) for v in raw["area_of_interest"])
    if not (west < east and south < north):
        raise ValueError(f"{path}: area_of_interest must be [west, south, east, north]")

    series = raw["series"]
    despeckle = raw.get("despeckle", {})
    det = raw.get("detection", {})
    defaults = Detection()
    detection = Detection(
        **{
            name: type(getattr(defaults, name))(det.get(name, getattr(defaults, name)))
            for name in Detection.__dataclass_fields__
        }
    )
    unknown = set(det) - set(Detection.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{path}: unknown detection setting(s) {sorted(unknown)}")
    return EventConfig(
        detection=detection,
        despeckle=Despeckle(
            lee_window=int(despeckle.get("lee_window", Despeckle.lee_window)),
            quegan_window=int(despeckle.get("quegan_window", Despeckle.quegan_window)),
            enl=float(despeckle.get("enl", Despeckle.enl)),
        ),
        project=raw["project"],
        event=raw["event"],
        area_of_interest=(west, south, east, north),
        window=Window(start=_as_date(raw["window"]["start"]), end=_as_date(raw["window"]["end"])),
        series=Series(
            collection=series["collection"],
            start=_as_date(series["start"]),
            end=_as_date(series["end"]),
            orbit_state=series["orbit_state"],
            instrument_mode=series["instrument_mode"],
            relative_orbit=int(series["relative_orbit"]),
            assets=tuple(series["assets"]),
        ),
    )


def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)
