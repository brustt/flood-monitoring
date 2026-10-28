"""Event config: the one file that says what an Event is and which Series it uses."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from shapely.geometry import MultiPolygon, Polygon

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
    relative_orbits: tuple[int, ...]  # one Series per relative orbit, all of *orbit_state*
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
class CompositeRanges:
    """Value range in dB stretched to 0-255 for each Composite band (red VV, green VH, blue VV minus VH).

    The same ranges go into the manifest's ``rgb`` hint.
    """

    vv: tuple[float, float] = (-20.0, -3.0)
    vh: tuple[float, float] = (-26.0, -9.0)
    vv_minus_vh: tuple[float, float] = (2.0, 12.0)

    def as_dict(self) -> dict[str, tuple[float, float]]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class EventGrid:
    """Grid of the global Layers. *crs* None means the CRS of the first Series (in config order)."""

    crs: str | None = None
    pixel_size: float = 10.0  # metres


@dataclass(frozen=True)
class Processing:
    """Memory budget of every stage after download; sets the chunk size and GDAL cache (see chunks.py)."""

    memory_budget_mb: float = 4096.0


@dataclass(frozen=True)
class EventConfig:
    project: str
    event: str
    area_of_interest: tuple[float, float, float, float]  # bounding box, used for searches and Subsets
    window: Window
    series: Series
    despeckle: Despeckle = Despeckle()
    detection: Detection = Detection()
    composite: CompositeRanges = CompositeRanges()
    event_grid: EventGrid = EventGrid()
    aoi_polygon: Polygon | MultiPolygon | None = None  # EPSG:4326; None for a rectangle Area of Interest
    processing: Processing = Processing()


def load_event_config(event: str, events_dir: Path = EVENTS_DIR) -> EventConfig:
    """Load the config of *event* from ``<events_dir>/<event>.yaml``."""
    path = events_dir / f"{event}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in events_dir.glob("*.yaml"))
        raise FileNotFoundError(f"no config for Event {event!r} in {events_dir} (known: {known})")
    raw = yaml.safe_load(path.read_text())
    if raw["event"] != event:
        raise ValueError(f"{path} declares Event {raw['event']!r}, expected {event!r}")

    series = raw["series"]
    orbit_state = series["orbit_state"]
    if orbit_state != "descending":
        raise ValueError(f"{path}: orbit_state must be 'descending' (one orbit state per Event), got {orbit_state!r}")
    orbits = series["relative_orbit"]
    relative_orbits = tuple(int(o) for o in orbits) if isinstance(orbits, list) else (int(orbits),)
    if not relative_orbits or len(set(relative_orbits)) != len(relative_orbits):
        raise ValueError(f"{path}: relative_orbit must list each orbit once")
    bbox, polygon = _area_of_interest(raw["area_of_interest"], path, events_dir.parent)
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
        composite=_composite_ranges(raw.get("composite", {}), path),
        despeckle=Despeckle(
            lee_window=int(despeckle.get("lee_window", Despeckle.lee_window)),
            quegan_window=int(despeckle.get("quegan_window", Despeckle.quegan_window)),
            enl=float(despeckle.get("enl", Despeckle.enl)),
        ),
        event_grid=_event_grid(raw.get("event_grid", {}), path),
        processing=_processing(raw.get("processing") or {}, path),
        project=raw["project"],
        event=raw["event"],
        area_of_interest=bbox,
        aoi_polygon=polygon,
        window=Window(start=_as_date(raw["window"]["start"]), end=_as_date(raw["window"]["end"])),
        series=Series(
            collection=series["collection"],
            start=_as_date(series["start"]),
            end=_as_date(series["end"]),
            orbit_state=orbit_state,
            instrument_mode=series["instrument_mode"],
            relative_orbits=relative_orbits,
            assets=tuple(series["assets"]),
        ),
    )


def _event_grid(raw: dict, path: Path) -> EventGrid:
    unknown = set(raw) - set(EventGrid.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{path}: unknown event_grid setting(s) {sorted(unknown)}")
    grid = EventGrid(crs=raw.get("crs"), pixel_size=float(raw.get("pixel_size", EventGrid.pixel_size)))
    if not grid.pixel_size > 0:
        raise ValueError(f"{path}: event_grid pixel_size must be positive")
    return grid


def _processing(raw: dict, path: Path) -> Processing:
    unknown = set(raw) - set(Processing.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{path}: unknown processing setting(s) {sorted(unknown)}")
    try:
        budget = float(raw.get("memory_budget_mb", Processing.memory_budget_mb))
    except (TypeError, ValueError):
        raise ValueError(f"{path}: processing memory_budget_mb must be a number") from None
    if not budget > 0:
        raise ValueError(f"{path}: processing memory_budget_mb must be positive")
    if not math.isfinite(budget):
        raise ValueError(f"{path}: processing memory_budget_mb must be finite")
    return Processing(memory_budget_mb=budget)


def _area_of_interest(raw, path: Path, base: Path):
    """Bounding box and polygon (None for a rectangle) of the ``area_of_interest`` setting.

    Either ``[west, south, east, north]`` in EPSG:4326, ``<file>``, a polygon vector file, or
    ``{line: <file>, buffer_m: <metres>}``, a buffer around a river line file. Files are relative to
    the repo root.
    """
    if isinstance(raw, str):
        vector = base / raw
        if not vector.is_file():
            raise FileNotFoundError(f"{path}: area_of_interest file {vector} not found")
        polygon = _read_polygon(vector)
        west, south, east, north = polygon.bounds
        return (west, south, east, north), polygon
    if isinstance(raw, dict):
        if set(raw) != {"line", "buffer_m"}:
            raise ValueError(f"{path}: area_of_interest buffer form needs exactly 'line' and 'buffer_m'")
        line = base / raw["line"]
        if not line.is_file():
            raise FileNotFoundError(f"{path}: river line file {line} not found")
        polygon = _buffer_line(line, float(raw["buffer_m"]))
        west, south, east, north = polygon.bounds
        return (west, south, east, north), polygon
    west, south, east, north = (float(v) for v in raw)
    if not (west < east and south < north):
        raise ValueError(f"{path}: area_of_interest must be [west, south, east, north]")
    return (west, south, east, north), None


def _read_polygon(vector: Path):
    """All geometries of *vector* merged, as an EPSG:4326 polygon."""
    import geopandas as gpd

    polygon = gpd.read_file(vector).to_crs(4326).union_all()
    if polygon.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{vector}: area_of_interest file must hold polygons, got {polygon.geom_type}")
    return polygon


def _buffer_line(line: Path, metres: float):
    """All geometries of *line* merged and buffered by *metres*, as an EPSG:4326 polygon."""
    import geopandas as gpd

    if not metres > 0:
        raise ValueError("buffer_m must be positive")
    lines = gpd.read_file(line).to_crs(4326)
    projected = lines.to_crs(lines.estimate_utm_crs())
    buffered = gpd.GeoSeries([projected.union_all().buffer(metres)], crs=projected.crs)
    return buffered.to_crs(4326).iloc[0]


def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _composite_ranges(raw: dict, path: Path) -> CompositeRanges:
    unknown = set(raw) - set(CompositeRanges.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{path}: unknown composite band(s) {sorted(unknown)}")
    ranges = {}
    for name, value in raw.items():
        low, high = (float(v) for v in value)
        if not low < high:
            raise ValueError(f"{path}: composite {name} range must be [low, high] with low < high")
        ranges[name] = (low, high)
    return CompositeRanges(**ranges)
