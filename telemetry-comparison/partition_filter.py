"""Build WHERE clauses from partition column values.

Used by /api/telemetry to filter DuckDB queries on the Hive-partitioned
Parquet files. The allowlist regex prevents SQL injection via `{val}` interpolation —
see tests/test_partition_filter.py.
"""

from __future__ import annotations

import re

# Allow-list for partition column values. The characters here cover every
# value we've seen in ac_telemetry (lower/upper/digits/underscore for
# environment/rig/experiment/driver/track/carModel, plus dash/dot/colon/space
# for session_id timestamp variants). Rejecting anything else prevents SQL
# injection via `{val}` interpolation in the WHERE clause.
_SAFE_PARTITION_VALUE = re.compile(r"^[A-Za-z0-9_\-.: ]+$")


def _build_partition_filter(**kwargs) -> str:
    """Build a WHERE clause from partition column values.

    Skips empty strings. Uses CAST for session_id to handle
    DuckDB timestamp normalization vs Hive partition format.

    Raises ValueError on any string value that doesn't match
    `_SAFE_PARTITION_VALUE`. Callers should translate that into a 400.
    """
    clauses = []
    for col, val in kwargs.items():
        if val is None or val == "":
            continue
        if isinstance(val, int):
            clauses.append(f"{col} = {val}")
            continue
        if not _SAFE_PARTITION_VALUE.fullmatch(str(val)):
            raise ValueError(f"Invalid character in {col}: {val!r}")
        if col == "session_id":
            # Hive partitions store session_id as e.g. "2026-04-14T11:42:08.107Z"
            # but the frontend may send "2026-04-14 11:42:08.107000" (space, microseconds, no Z).
            # Use CAST to VARCHAR + LIKE prefix match to handle all format variations.
            # Strip trailing zeros and Z to get a common prefix for matching.
            prefix = val.replace("T", " ").rstrip("Z").rstrip("0").rstrip(".")
            clauses.append(f"CAST(session_id AS VARCHAR) LIKE '{prefix}%'")
        else:
            clauses.append(f"{col} = '{val}'")
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""
