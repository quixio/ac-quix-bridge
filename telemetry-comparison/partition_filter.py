"""Build WHERE clauses from partition column values.

Used by /api/telemetry to filter DuckDB queries on the Hive-partitioned
Parquet files. Partition values are interpolated into SQL string literals, so
single quotes are doubled (`''`) to keep a value from breaking out of the
literal — the same escaping the frontend Lakehouse embed and leaderboard use.
That makes injection inert without an ASCII allowlist, so accented / Unicode
names that legitimately exist as lake partitions (e.g. `daniel laštic`,
`Petr Čech`, `O'Brien`) build a query instead of being rejected. Only control
characters are refused — they are never valid partition keys. See
tests/test_partition_filter.py.
"""

from __future__ import annotations

import re

# Control characters are never legitimate partition values; everything
# printable is allowed and made injection-safe via single-quote doubling.
_FORBIDDEN_PARTITION_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _quote(val: str) -> str:
    """Double single quotes so `val` is safe inside a '...' SQL string literal."""
    return val.replace("'", "''")


def _build_partition_filter(**kwargs) -> str:
    """Build a WHERE clause from partition column values.

    Skips empty strings. session_id is re-canonicalized to the stored ISO-Z
    form and matched with `=` (so the catalog can prune partitions). String
    values are single-quote-escaped before interpolation.

    Raises ValueError on any string value containing a control character.
    Callers should translate that into a 400.
    """
    clauses = []
    for col, val in kwargs.items():
        if val is None or val == "":
            continue
        if isinstance(val, int):
            clauses.append(f"{col} = {val}")
            continue
        sval = str(val)
        if _FORBIDDEN_PARTITION_CHARS.search(sval):
            raise ValueError(f"Invalid character in {col}: {val!r}")
        if col == "session_id":
            # Stored partition path is ISO-8601 with trailing Z, e.g.
            # "2026-06-18T08:31:11.764Z". DuckDB infers the partition as
            # TIMESTAMP and *displays* it space-separated without the Z, but the
            # catalog prunes on the raw path string — so re-canonicalize to the
            # stored form and match with `=`. A CAST(session_id AS VARCHAR)/LIKE
            # predicate is not push-down-able, so it defeats partition pruning
            # and scans every session under the other partitions (~7x slower).
            norm = sval.replace(" ", "T")
            if not norm.endswith("Z"):
                norm += "Z"
            clauses.append(f"session_id = '{_quote(norm)}'")
        else:
            clauses.append(f"{col} = '{_quote(sval)}'")
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""
