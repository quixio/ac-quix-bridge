"""Enumerate session combinations via the Iceberg catalog /manifest endpoint.

One catalog call (~130 ms, size-independent) returns every file's
partition_values; we dedupe client-side to produce one row per distinct
session combo (env, rig, experiment, driver, track, carModel, session_id),
each with the set of recorded laps.

Replaces the previous lake /partitions tree walk (D × ~150 ms per request)
since the catalog has an indexed manifest_entries table optimized for this.

Config values are read off the `config` module at call time so tests can
monkeypatch `config.LAKEHOUSE_CATALOG_URL` / `config.LAKEHOUSE_CATALOG_TOKEN` to simulate
missing vars. The httpx client is module-level so the TLS handshake +
connection pool are amortized across requests.
"""

from __future__ import annotations

from collections import defaultdict

import httpx

import config

PARTITION_COLS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
]

_http_client = httpx.AsyncClient(
    timeout=30.0,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)


async def _list_session_combinations(
    filters: dict[str, str] | None = None,
) -> list[dict]:
    """Return distinct session combinations from the catalog manifest.

    `filters` (e.g. `{"driver": "patrick"}`) trim the result client-side
    after dedupe — the manifest is fetched in full either way, but the
    response shaved before returning. Catalog is fast enough that the
    optimization of pushing filters down isn't worth the API complexity.
    """
    if not config.LAKEHOUSE_CATALOG_URL or not config.LAKEHOUSE_CATALOG_TOKEN:
        missing = [
            name
            for name, val in (
                ("LAKEHOUSE_CATALOG_URL", config.LAKEHOUSE_CATALOG_URL),
                ("LAKEHOUSE_CATALOG_TOKEN", config.LAKEHOUSE_CATALOG_TOKEN),
            )
            if not val
        ]
        raise RuntimeError(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Set them in .env or the environment before starting the service."
        )

    response = await _http_client.get(
        f"{config.LAKEHOUSE_CATALOG_URL}/namespaces/default/tables/{config.TABLE_NAME}/manifest",
        headers={"Authorization": f"Bearer {config.LAKEHOUSE_CATALOG_TOKEN}"},
    )
    response.raise_for_status()
    entries = response.json().get("entries") or []

    lap_map: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for entry in entries:
        pv = entry.get("partition_values") or {}
        if not pv:
            continue
        key = tuple(str(pv.get(col, "")) for col in PARTITION_COLS)
        lap_val = pv.get("lap")
        if lap_val is not None and str(lap_val).isdigit():
            lap_map[key].add(int(lap_val))
        else:
            lap_map.setdefault(key, set())

    sessions: list[dict] = []
    for key, laps in lap_map.items():
        session = dict(zip(PARTITION_COLS, key))
        session["laps"] = sorted(laps)
        if filters and not all(session.get(k) == v for k, v in filters.items()):
            continue
        sessions.append(session)
    return sessions
