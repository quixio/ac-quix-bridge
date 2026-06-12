"""Lake-first enumeration of leaderboard partition groups.

Single public function: `enumerate_groups()` — the distinct
`(track, carModel, experiment, environment)` tuples that have completed
laps in the configured lake table. `live_telemetry._known_groups()`
unions this with its live-session/DCM-derived groups so the historical
leaderboard populates straight from the lake, with no live AC session,
no DCM config, and no Mongo data required.

Two enumeration paths, both against the QuixLake API the service
already has credentials for (`Quix__Lakehouse__Query__Url` / `__AuthToken`):

  1. **Primary — partitions endpoint + pruned fan-out.**
     `GET {base}/partitions?table={table}` (QuixLake's Iceberg-catalog-
     backed partition-tree endpoint; metadata only, no data rows scanned)
     discovers the distinct `environment` values, then one
     `SELECT experiment, track, carModel FROM {table}
      WHERE environment = '<env>' AND {best_col} > 0
      GROUP BY experiment, track, carModel`
     per environment, run in parallel. The equality predicate triggers
     server-side partition pruning, so a stray non-partitioned
     `data.parquet` at the table root (present in at least one lake
     instance, intentionally not deleted) never enters the scan list.
  2. **Fallback — single dashboard-shape global GROUP BY** when the
     `/partitions` endpoint is unavailable (older QuixLake builds).
     The same query shape the telemetry-dashboard runs in production
     (`telemetry-dashboard/main.py`), widened with the group columns:
     `SELECT environment, experiment, track, carModel, driver,
      MIN({best_col}) AS ms FROM {table}
      WHERE {best_col} > 0 AND driver IS NOT NULL AND driver <> ''
      GROUP BY environment, experiment, track, carModel, driver`,
     reduced to distinct group tuples in Python. Un-pruned, so on a lake
     with the stray root parquet it fails at bind time with
     `Binder Error: Hive partition mismatch` — that error is swallowed
     by the caching layer (stale-on-error / `[]`), which is the accepted
     degradation on such lakes; the cloud-global lake binds it fine.

Why not pyiceberg against `Quix__Lakehouse__Catalog__Url`: the
`/partitions` endpoint reads the same Iceberg catalog metadata, uses
credentials that are provably configured (the `/query` calls already
work), needs no new dependency, and is verifiable outside the cloud.

Caching: module-level TTL cache (`settings.partition_index_ttl_seconds`,
default 60 s) with stale-on-error (a failed refresh serves the previous
result and backs off one TTL) and single-flight (concurrent callers
queue behind the in-flight refresh, then re-read the fresh cache).
`enumerate_groups()` never raises — total failure with no prior result
returns `[]`.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .lakehouse_client import LakehouseClient
from .settings import get_settings

logger = logging.getLogger(__name__)

# Metadata-endpoint timeout. The partition tree read is catalog metadata
# only, so it answers in well under a second when healthy.
_PARTITIONS_TIMEOUT_S = 10.0

_lock = threading.Lock()  # guards the three cache fields below
_refresh_lock = threading.Lock()  # single-flight for the lake round-trips
_cached_groups: list[tuple[str, str, str, str]] | None = None
_cached_at_monotonic: float = 0.0
_failed_at_monotonic: float | None = None


def _sql_quote(value: str) -> str:
    """Single-quote-escape for inline SQL literals (ANSI '' doubling).

    Local copy of `routes.leaderboard_real._format_sql_string` — importing
    it would pull the whole assembly module (and its `live_telemetry`
    import) into this leaf module.
    """
    return value.replace("'", "''")


def _fetch_environments_via_partitions_endpoint(
    base_url: str, token: str, lake_table: str
) -> list[str]:
    """Distinct `environment` partition values via `GET /partitions`.

    Metadata-only (Iceberg catalog manifest), one HTTP call. Raises on
    any transport/shape problem so the caller can fall through to SQL.
    """
    url = f"{base_url.rstrip('/')}/partitions"
    # verify=False: demo Box Cloud self-signed certs — same TODO(ssl) as
    # `LakehouseClient.query`.
    with httpx.Client(verify=False) as client:
        r = client.get(
            url,
            params={"table": lake_table},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_PARTITIONS_TIMEOUT_S,
        )
    r.raise_for_status()
    body = r.json()
    partitions = body.get("partitions")
    if not isinstance(partitions, list):
        raise ValueError(f"unexpected /partitions response shape: {body!r}")
    envs: list[str] = []
    for entry in partitions:
        name = str(entry.get("name") or "")
        prefix, sep, value = name.partition("=")
        # `environment=` (empty value) is the stray root data.parquet
        # surfacing as a pseudo-partition — skip it.
        if prefix == "environment" and sep and value.strip():
            envs.append(value.strip())
    return envs


def _build_global_groups_sql(lake_table: str, best_col: str) -> str:
    """Dashboard-shape global GROUP BY for the fallback path.

    Mirrors the telemetry-dashboard's proven production query (same
    `{best_col} > 0 AND driver` filter, single-level GROUP BY — no CTE,
    QuixLake silently returns 0 rows for `WITH ...`), with the group
    columns added. `driver`/`MIN(ms)` are part of the proven shape but
    unused by the reduction.
    """
    return (
        f"SELECT environment, experiment, track, carModel, driver, "
        f'MIN("{best_col}") AS ms '
        f"FROM {lake_table} "
        f'WHERE "{best_col}" > 0 AND driver IS NOT NULL AND driver <> \'\' '
        f"GROUP BY environment, experiment, track, carModel, driver"
    )


def _fetch_groups_global(
    client: LakehouseClient, lake_table: str, best_col: str
) -> list[tuple[str, str, str, str]]:
    """Distinct group tuples via one un-pruned global GROUP BY.

    Reduces the per-driver rows to distinct
    `(track, carModel, experiment, environment)` tuples, dropping rows
    missing any of the four fields (the CSV response parses the lake's
    `NA` placeholder partitions to NaN, `fillna("")` folds them to empty
    strings, and the guard drops them — a placeholder group can't render
    meaningfully anyway).
    """
    df = client.query(_build_global_groups_sql(lake_table, best_col)).fillna("")
    seen: set[tuple[str, str, str, str]] = set()
    groups: list[tuple[str, str, str, str]] = []
    for row in df.to_dict("records"):
        environment = str(row.get("environment") or "").strip()
        experiment = str(row.get("experiment") or "").strip()
        track = str(row.get("track") or "").strip()
        car = str(row.get("carModel") or "").strip()
        if not (environment and experiment and track and car):
            continue
        key = (track, car, experiment, environment)
        if key not in seen:
            seen.add(key)
            groups.append(key)
    return groups


def _fetch_groups_for_environment(
    client: LakehouseClient, lake_table: str, best_col: str, environment: str
) -> list[tuple[str, str, str, str]]:
    """`(track, carModel, experiment, environment)` tuples for one env.

    Single-level GROUP BY (no CTE — QuixLake silently returns 0 rows for
    `WITH ...`), equality partition predicate (dodges the stray-file
    Binder error), `{best_col} > 0` so only groups with at least one
    completed lap enumerate — a group without a flying lap has nothing
    to put on the board anyway.

    `NA` placeholder partitions (the lake sink's missing-enrichment
    marker): the CSV response goes through `pandas.read_csv`, which
    parses the literal `NA` as NaN; `fillna("")` then folds it to the
    empty string and the empty-field guard drops the row. A group with a
    placeholder experiment/track/carModel can't render meaningfully
    anyway. (Environment values are unaffected — they come from the JSON
    metadata endpoint, which preserves `"NA"`, so real data recorded
    under `environment=NA` still enumerates.)
    """
    sql = (
        f"SELECT experiment, track, carModel FROM {lake_table} "
        f"WHERE environment = '{_sql_quote(environment)}' "
        f"AND {best_col} > 0 "
        f"GROUP BY experiment, track, carModel"
    )
    df = client.query(sql).fillna("")
    groups: list[tuple[str, str, str, str]] = []
    for row in df.to_dict("records"):
        experiment = str(row.get("experiment") or "").strip()
        track = str(row.get("track") or "").strip()
        car = str(row.get("carModel") or "").strip()
        if not (experiment and track and car):
            continue
        groups.append((track, car, experiment, environment))
    return groups


def _enumerate_from_lake() -> list[tuple[str, str, str, str]]:
    """One full enumeration round-trip. Raises on failure.

    Any per-environment query failure fails the whole enumeration —
    a partial group list would make `refresh_best_laps_cache` prune
    cached groups that still exist in the lake (flicker), so all-or-
    nothing with stale-on-error is the safer contract.
    """
    settings = get_settings()
    base_url = settings.lakehouse_query_url
    token = settings.lakehouse_query_token
    if not base_url or not token:
        raise RuntimeError("Lakehouse credentials not configured")
    lake_table = settings.lake_table
    best_col = settings.col_best_time
    client = LakehouseClient(base_url=base_url, token=token)

    try:
        environments = _fetch_environments_via_partitions_endpoint(
            base_url, token, lake_table
        )
    except Exception as e:
        logger.warning(
            "partition-index: /partitions metadata endpoint failed (%s); "
            "falling back to single global GROUP BY",
            e,
        )
        groups = _fetch_groups_global(client, lake_table, best_col)
        logger.info(
            "partition-index: enumerated %d group(s) via global GROUP BY fallback",
            len(groups),
        )
        return groups

    if not environments:
        logger.info(
            "partition-index: 0 environments in lake table %r "
            "(via partitions-endpoint)",
            lake_table,
        )
        return []
    seen: set[tuple[str, str, str, str]] = set()
    groups: list[tuple[str, str, str, str]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(environments))) as pool:
        per_env = pool.map(
            lambda env: _fetch_groups_for_environment(
                client, lake_table, best_col, env
            ),
            environments,
        )
        for env_groups in per_env:
            for key in env_groups:
                if key not in seen:
                    seen.add(key)
                    groups.append(key)

    logger.info(
        "partition-index: enumerated %d group(s) across %d environment(s) "
        "(partitions-endpoint discovery, per-env pruned GROUP BY)",
        len(groups),
        len(environments),
    )
    return groups


def enumerate_groups() -> list[tuple[str, str, str, str]]:
    """Return the lake's distinct `(track, carModel, experiment,
    environment)` group tuples, TTL-cached.

    Never raises. Failure modes:
      * lake unreachable / query error → previous result (stale-on-error)
        or `[]` when nothing was ever enumerated; retries back off one TTL.
      * credentials not configured → `[]`.
    """
    global _cached_groups, _cached_at_monotonic, _failed_at_monotonic

    settings = get_settings()
    ttl = settings.partition_index_ttl_seconds
    now = time.monotonic()

    with _lock:
        if _cached_groups is not None and now - _cached_at_monotonic < ttl:
            return list(_cached_groups)
        if _failed_at_monotonic is not None and now - _failed_at_monotonic < ttl:
            return list(_cached_groups) if _cached_groups is not None else []

    with _refresh_lock:
        # Double-check after waiting: another caller may have refreshed.
        now = time.monotonic()
        with _lock:
            if _cached_groups is not None and now - _cached_at_monotonic < ttl:
                return list(_cached_groups)
            if _failed_at_monotonic is not None and now - _failed_at_monotonic < ttl:
                return list(_cached_groups) if _cached_groups is not None else []
        try:
            groups = _enumerate_from_lake()
        except Exception:
            logger.exception(
                "partition-index: enumeration failed; serving %s",
                "previous result (stale-on-error)"
                if _cached_groups is not None
                else "empty group list",
            )
            with _lock:
                _failed_at_monotonic = time.monotonic()
                return list(_cached_groups) if _cached_groups is not None else []
        with _lock:
            _cached_groups = groups
            _cached_at_monotonic = time.monotonic()
            _failed_at_monotonic = None
            return list(groups)
