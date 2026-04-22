"""Partition tree walker + WHERE builder for the QuixLake `ac_telemetry` table.

Merged from telemetry-comparison's partition_walker.py + partition_filter.py.
Both are small and always used together here; one file keeps imports tidy.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from . import config

PARTITION_COLS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
]

# Allow-list for partition column values. Same set telemetry-comparison uses
# — rejects anything outside [A-Za-z0-9_\-.: ] to prevent SQL injection via
# `{val}` interpolation below.
_SAFE_PARTITION_VALUE = re.compile(r"^[A-Za-z0-9_\-.: ]+$")

_http_client = httpx.AsyncClient(
    timeout=30.0,
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
)

_LAKE_CONCURRENCY = 20
_lake_semaphore: asyncio.Semaphore | None = None


def _get_lake_semaphore() -> asyncio.Semaphore:
    global _lake_semaphore
    if _lake_semaphore is None:
        _lake_semaphore = asyncio.Semaphore(_LAKE_CONCURRENCY)
    return _lake_semaphore


async def _list_partition_children(path: str) -> list[str]:
    if not config.QUIXLAKE_URL or not config.QUIX_LAKE_TOKEN:
        raise RuntimeError(
            "Missing QUIXLAKE_URL / QUIX_LAKE_TOKEN — set them in .env before starting."
        )
    params = (
        {"table": config.TABLE_NAME, "path": path}
        if path
        else {"table": config.TABLE_NAME}
    )
    async with _get_lake_semaphore():
        response = await _http_client.get(
            f"{config.QUIXLAKE_URL}/partitions",
            params=params,
            headers={"Authorization": f"Bearer {config.QUIX_LAKE_TOKEN}"},
        )
    response.raise_for_status()
    return [p["name"] for p in response.json().get("partitions", [])]


async def walk_partition_tree(
    path: str = "", depth: int = 0, filters: dict[str, str] | None = None
) -> list[dict]:
    """Walk the partition tree; at the leaf attach a sorted `laps` list."""
    if depth == len(PARTITION_COLS):
        session: dict = {}
        for part in path.split("/"):
            if "=" in part:
                k, v = part.split("=", 1)
                session[k] = v
        lap_names = await _list_partition_children(path)
        laps: list[int] = []
        for name in lap_names:
            if name.startswith("lap="):
                try:
                    laps.append(int(name[len("lap=") :]))
                except ValueError:
                    continue
        session["laps"] = sorted(laps)
        return [session]

    children = await _list_partition_children(path)
    if not children:
        return []

    if filters:
        col = PARTITION_COLS[depth]
        wanted = filters.get(col)
        if wanted:
            target = f"{col}={wanted}"
            children = [c for c in children if c == target]

    next_paths = [f"{path}/{child}" if path else child for child in children]
    subtrees = await asyncio.gather(
        *(walk_partition_tree(p, depth + 1, filters) for p in next_paths)
    )
    return [s for sublist in subtrees for s in sublist]


def build_partition_filter(**kwargs: str | int | None) -> str:
    """Build a WHERE clause from partition column values.

    Skips empty strings. Uses CAST+LIKE for session_id to tolerate Hive's
    "2026-04-14T11:42:08.107Z" vs DuckDB's "2026-04-14 11:42:08.107000" formats.
    Raises ValueError on any value outside the allow-list.
    """
    clauses: list[str] = []
    for col, val in kwargs.items():
        if val is None or val == "":
            continue
        if isinstance(val, int):
            clauses.append(f"{col} = {val}")
            continue
        if not _SAFE_PARTITION_VALUE.fullmatch(str(val)):
            raise ValueError(f"Invalid character in {col}: {val!r}")
        if col == "session_id":
            prefix = str(val).replace("T", " ").rstrip("Z").rstrip("0").rstrip(".")
            # LIKE escape (\, %, _) then standard SQL single-quote escape ('').
            escaped = (
                prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            escaped = escaped.replace("'", "''")
            clauses.append(f"CAST(session_id AS VARCHAR) LIKE '{escaped}%' ESCAPE '\\'")
        else:
            # Belt + braces — the allow-list already blocks `'` but we double
            # any surviving single quote so the interpolation can't break out
            # of the string literal even if the regex is widened later.
            quoted = str(val).replace("'", "''")
            clauses.append(f"{col} = '{quoted}'")
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""
