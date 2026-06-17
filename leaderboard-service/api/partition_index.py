"""Lake-first enumeration of leaderboard partition groups.

Single public function: `enumerate_groups()` — the distinct
`(track, carModel, experiment, environment)` tuples that have completed
laps in the configured lake table. `live_telemetry._known_groups()`
unions this with its live-session/DCM-derived groups so the historical
leaderboard populates straight from the lake, with no live AC session,
no DCM config, and no Mongo data required.

Three enumeration paths, in preference order:

  1. **Primary — Iceberg catalog `/manifest` (metadata only, one call).**
     `GET {catalog}/namespaces/default/tables/{table}/manifest` returns
     one entry per data file, each carrying the file's full
     `partition_values` dict (`environment`, `track`, `carModel`,
     `experiment`, `driver`, `session_id`, `lap`, ...). We dedupe those
     in Python into the distinct `(track, carModel, experiment,
     environment)` group tuples. This is the same call (and the same
     dedupe-in-Python shape) the Telemetry Explorer uses
     (`telemetry-comparison/partition_walker.py`) and it answers in
     ~130 ms regardless of lake size. **No aggregation SQL runs.** This is
     the path that replaced the per-environment `GROUP BY` fan-out, which
     scanned data and hit the 30 s client timeout on the byox lake
     (`enumeration failed; serving empty group list`).

     Best-lap / completed-lap filter: `iBestTime > 0` is a *data* column,
     not a partition value, so it cannot be expressed in catalog metadata.
     We approximate the intent by requiring the partition combo to have at
     least one real lap (`lap >= 1`) — the lake sink partitions every
     completed lap by its number, so a combo with a lap partition has
     recorded telemetry for that lap. A combo that genuinely has no flying
     lap time (e.g. an out-lap only) can't be distinguished without a data
     scan; such a combo would surface here but render empty best-laps
     downstream, which the best-laps cache already tolerates.

  2. **Fallback A — `/partitions` endpoint + pruned per-env fan-out**
     (used only when the catalog is not configured). QuixLake's
     `GET {base}/partitions?table={table}` returns one partition *level*
     per call (`[{"name": "environment=<v>"}, ...]`), so it discovers the
     distinct `environment` values; then one
     `SELECT experiment, track, carModel FROM {table}
      WHERE environment = '<env>' AND {best_col} > 0
      GROUP BY experiment, track, carModel`
     per environment, run in parallel. **This GROUP BY is the path that
     timed out** — it is now only reachable when no catalog URL/token is
     configured (no metadata alternative available).
  3. **Fallback B — single dashboard-shape global GROUP BY** when the
     `/partitions` endpoint is also unavailable. The same query shape the
     telemetry-dashboard runs in production, widened with the group
     columns, reduced to distinct group tuples in Python.

Why the catalog `/manifest` over the `/partitions` tree walk: `/partitions`
is one level deep per call, so building full group tuples from it requires
a D-deep recursive walk (D × ~150 ms; see
`quix-ai-config/ac-telemetry-agent/make_kb_files.py:walk`). The catalog's
indexed manifest returns every file's partition_values in a single
size-independent call instead.

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

from .lakehouse_client import _is_retryable
from .lakehouse_client import LakehouseClient
from .settings import get_settings

logger = logging.getLogger(__name__)

# Metadata-endpoint timeout. The partition tree / manifest reads are
# catalog metadata only, so they answer in well under a second when healthy.
_PARTITIONS_TIMEOUT_S = 10.0

# Bounded retry for the metadata GETs (manifest / partitions), mirroring
# `LakehouseClient.query`. Transient failures (timeout / transport / 5xx)
# retry with short backoffs, then raise — `_enumerate_from_lake`'s caller
# (`enumerate_groups`) catches that and serves the stale/empty group list.
_METADATA_RETRY_BACKOFFS_S = (0.5, 1.0)


def _get_with_retry(
    url: str,
    *,
    params: dict[str, str] | None,
    headers: dict[str, str],
) -> httpx.Response:
    """GET *url* with the same bounded-retry policy as the query client.

    Returns the raised-for-status response on success. Re-raises the last
    exception once attempts are exhausted (or immediately for a
    non-retryable error). One short WARNING per transient failure — no
    per-attempt traceback spam.
    """
    attempts = len(_METADATA_RETRY_BACKOFFS_S) + 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            # verify=False: demo Box Cloud self-signed certs — same TODO(ssl)
            # as `LakehouseClient.query`.
            with httpx.Client(verify=False) as client:
                r = client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=_PARTITIONS_TIMEOUT_S,
                )
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001 — classified below
            last_exc = exc
            if not _is_retryable(exc) or attempt == attempts - 1:
                raise
            backoff = _METADATA_RETRY_BACKOFFS_S[attempt]
            logger.warning(
                "lake metadata GET transient failure (attempt %d/%d): %s: %s "
                "— retrying in %.1fs",
                attempt + 1,
                attempts,
                type(exc).__name__,
                exc,
                backoff,
            )
            time.sleep(backoff)
    assert last_exc is not None
    raise last_exc

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
    r = _get_with_retry(
        url,
        params={"table": lake_table},
        headers={"Authorization": f"Bearer {token}"},
    )
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


def _fetch_groups_via_catalog_manifest(
    catalog_url: str, catalog_token: str, lake_table: str
) -> list[tuple[str, str, str, str]]:
    """Distinct `(track, carModel, experiment, environment)` tuples via
    the Iceberg catalog `/manifest` endpoint.

    One metadata-only HTTP call (size-independent, ~130 ms). Each manifest
    entry carries the file's full `partition_values`; we dedupe them in
    Python — no aggregation SQL, no data scan. Mirrors
    `telemetry-comparison/partition_walker.py`.

    Completed-lap filter: `lap` is a partition value, but `iBestTime > 0`
    is not, so we require `lap >= 1` (a combo with at least one recorded
    lap partition). A combo missing any of the four group fields, or with
    no real lap, is dropped. `NA` placeholder partitions surface as the
    literal string `"NA"` here (the JSON response preserves it), matching
    the existing per-env path's treatment of `environment=NA`; combos
    whose track/car/experiment is empty are dropped by the field guard.

    Raises on any transport/shape problem so the caller can fall through
    to the `/partitions` + GROUP BY path.
    """
    url = (
        f"{catalog_url.rstrip('/')}/namespaces/default/tables/"
        f"{lake_table}/manifest"
    )
    r = _get_with_retry(
        url,
        params=None,
        headers={"Authorization": f"Bearer {catalog_token}"},
    )
    body = r.json()
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"unexpected /manifest response shape: {body!r}")

    seen: set[tuple[str, str, str, str]] = set()
    groups: list[tuple[str, str, str, str]] = []
    for entry in entries:
        pv = entry.get("partition_values") or {}
        if not isinstance(pv, dict):
            continue
        # Completed-lap proxy: require a real numbered lap partition.
        lap_val = pv.get("lap")
        if lap_val is None or not str(lap_val).isdigit() or int(lap_val) < 1:
            continue
        track = str(pv.get("track") or "").strip()
        car = str(pv.get("carModel") or "").strip()
        experiment = str(pv.get("experiment") or "").strip()
        environment = str(pv.get("environment") or "").strip()
        if not (track and car and experiment and environment):
            continue
        key = (track, car, experiment, environment)
        if key not in seen:
            seen.add(key)
            groups.append(key)
    return groups


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
    lake_table = settings.lake_table
    best_col = settings.col_best_time
    catalog_url = settings.lakehouse_catalog_url
    catalog_token = settings.lakehouse_catalog_token

    # Primary: the catalog `/manifest` metadata call — one round-trip, no
    # aggregation SQL, no data scan. This is the path that replaces the
    # per-env GROUP BY fan-out that hit the 30 s timeout.
    if catalog_url and catalog_token:
        groups = _fetch_groups_via_catalog_manifest(
            catalog_url, catalog_token, lake_table
        )
        logger.info(
            "partition-index: enumerated %d group(s) via catalog /manifest "
            "(metadata-only, no GROUP BY)",
            len(groups),
        )
        return groups

    # No catalog configured — fall back to the query API. Both fallbacks
    # below run aggregation SQL and exist only for deployments without
    # catalog credentials.
    logger.warning(
        "partition-index: no catalog URL/token configured; falling back to "
        "the /partitions + per-env GROUP BY path (slower, may time out on "
        "large lakes). Set Quix__Lakehouse__Catalog__Url / __AuthToken to "
        "use the fast manifest path."
    )
    base_url = settings.lakehouse_query_url
    token = settings.lakehouse_query_token
    if not base_url or not token:
        raise RuntimeError("Lakehouse credentials not configured")
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


def cached_groups() -> list[tuple[str, str, str, str]] | None:
    """Return the last-enumerated group list WITHOUT ever hitting the lake.

    Non-blocking, lake-free read of the module-level cache: returns the
    cached groups (even if stale) when an enumeration has ever succeeded,
    else ``None`` (never enumerated yet). Used by the hot Kafka-consumer
    path (`live_telemetry._resolve_session_experiment` on a raw tick) so a
    cold or slow lake can never block raw consumption — the background
    lake-work executor warms this cache via `enumerate_groups()`, and the
    hot path reads whatever is ready.
    """
    with _lock:
        return list(_cached_groups) if _cached_groups is not None else None


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
