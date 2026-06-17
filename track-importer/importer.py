"""Parse bundled AC track-layout CSV/JSON into track_layouts documents.

A layout is in scope iff a `layout_<layout>_corners.json` exists for it under
`<data_dir>/<track>/`; the matching `layout_<layout>.csv` is required (error if
missing). Suffixed sim/ideal-line CSVs are ignored because they have no
corners file gating them in.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import bson

# Mongo hard document-size limit. Warn when a doc gets within this fraction.
MAX_DOC_BYTES = 16 * 1024 * 1024
WARN_DOC_FRACTION = 0.5

SOURCE = "LapTimeEstimator fast_lane v7"

# CSV columns parsed as plain floats (never null in practice).
_FLOAT_COLS = (
    "distance_m",
    "segment_length_m",
    "x",
    "y",
    "z",
    "elevation_m",
    "gradient_pct",
    "radius_m",
    "width_left_m",
    "width_right_m",
    "width_total_m",
)
# Columns that may be blank in the CSV and must map to null, not 0.
_NULLABLE_FLOAT_COLS = ("speed_ms", "speed_kmh")


def _to_float_or_none(value: str) -> float | None:
    value = value.strip()
    if value == "":
        return None
    return float(value)


def _parse_points(csv_path: Path) -> list[dict]:
    """Read a layout CSV into a list of typed point dicts."""
    points: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            point: dict[str, float | int | None] = {
                "index": int(row["index"]),
            }
            for col in _FLOAT_COLS:
                point[col] = float(row[col])
            for col in _NULLABLE_FLOAT_COLS:
                point[col] = _to_float_or_none(row[col])
            points.append(point)
    return points


def discover_layouts(data_dir: Path) -> dict[str, list[tuple[str, Path, Path]]]:
    """Group in-scope layouts by track.

    Returns {track: [(layout, csv_path, corners_path), ...]} sorted by track
    then layout. Raises if a corners file lacks its matching CSV.
    """
    by_track: dict[str, list[tuple[str, Path, Path]]] = {}
    for corners_path in sorted(data_dir.glob("*/layout_*_corners.json")):
        track = corners_path.parent.name
        # layout_<layout>_corners.json -> <layout>
        layout = corners_path.name[len("layout_") : -len("_corners.json")]
        csv_path = corners_path.with_name(f"layout_{layout}.csv")
        if not csv_path.exists():
            raise FileNotFoundError(
                f"corners file {corners_path} has no matching CSV {csv_path}"
            )
        by_track.setdefault(track, []).append((layout, csv_path, corners_path))
    for layouts in by_track.values():
        layouts.sort(key=lambda item: item[0])
    return dict(sorted(by_track.items()))


def derive_track_configuration(
    track: str,
    layout: str,
    layout_count: int,
    config_map: dict[str, str],
) -> tuple[str, bool]:
    """Derive the AC trackConfiguration join string.

    Returns (trackConfiguration, used_heuristic).

    Precedence:
      1. Explicit override in config_map ({"<track>/<layout>": "<config>"}).
      2. Heuristic: multi-layout track -> trackConfiguration = layout;
         single-layout track -> trackConfiguration = "track config" (the
         literal string AC's shared memory reports for layout-less tracks).
    """
    doc_id = f"{track}/{layout}"
    if doc_id in config_map:
        return config_map[doc_id], False
    if layout_count > 1:
        return layout, True
    return "track config", True


def build_document(
    track: str,
    layout: str,
    csv_path: Path,
    corners_path: Path,
    track_configuration: str,
    imported_at: datetime,
) -> dict:
    """Assemble one track_layouts document."""
    points = _parse_points(csv_path)

    corners_doc = json.loads(corners_path.read_text(encoding="utf-8"))
    corners = corners_doc.get("corners", [])

    # length_m: prefer corners.json total_length_m; else max distance_m.
    length_m = corners_doc.get("total_length_m")
    if length_m is None:
        length_m = max((p["distance_m"] for p in points), default=0.0)

    doc: dict = {
        "_id": f"{track}/{layout}",
        "track": track,
        "trackConfiguration": track_configuration,
        "layout": layout,
        "length_m": float(length_m),
        "n_points": len(points),
        "n_corners": len(corners),
        "source": SOURCE,
        "imported_at": imported_at,
        "points": points,
        "corners": corners,
        # `config` here is the corner-detection thresholds from the source
        # JSON, not the AC config string.
        "corners_meta": {
            "config": corners_doc.get("config"),
            "generated_at": corners_doc.get("generated_at"),
            "version": corners_doc.get("version"),
        },
    }
    return doc


def doc_bson_size(doc: dict) -> int:
    """BSON-encoded size in bytes. Asserts the 16 MB hard limit."""
    size = len(bson.encode(doc))
    if size >= MAX_DOC_BYTES:
        raise ValueError(
            f"document {doc['_id']!r} is {size} bytes, exceeds Mongo's "
            f"{MAX_DOC_BYTES}-byte limit"
        )
    return size


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
