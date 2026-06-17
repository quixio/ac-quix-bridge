---
name: lake-query-chain
description: End-to-end map of how the Telemetry Explorer (telemetry-comparison) talks to QuixLake — config/token resolution, reading partitions from the Iceberg catalog manifest (dropdowns), and building/running the DuckDB SQL telemetry query from the dropdown selection. Use when modifying /api/sessions, /api/telemetry, the cascading dropdowns, the WHERE-clause builder, or debugging lake 500/502/504 errors.
---

# Lake query chain (Telemetry Explorer ↔ QuixLake)

Two independent lake-backed flows, plus shared config. The track MAP is a third,
separate flow → see the `track-geometry-mongo` skill (Mongo, not the lake).

```
                         ┌─ DROPDOWNS  (partitions)  ── Iceberg catalog /manifest
config.py (URLs+tokens) ─┤
                         └─ TELEMETRY  (lap data)    ── DuckDB POST /query
```

## 1. Config + token resolution — `config.py`

Byox-injected names win; legacy names are the local-dev fallback:

```python
QUIXLAKE_URL    = getenv("Quix__Lakehouse__Query__Url")       or getenv("QUIXLAKE_URL")
QUIX_LAKE_TOKEN = getenv("Quix__Lakehouse__Query__AuthToken") or getenv("QUIX_LAKE_TOKEN")
CATALOG_URL     = getenv("Quix__Lakehouse__Catalog__Url")     or getenv("CATALOG_URL")
CATALOG_TOKEN   = getenv("Quix__Lakehouse__Catalog__AuthToken") or getenv("CATALOG_TOKEN")
TABLE_NAME      = getenv("TABLE_NAME", "ac_telemetry")
```

- `Quix__Lakehouse__Query__Url` is auto-injected **regardless of `blobStorage: bind`** — the deployed Explorer needs nothing in its variables. (`QUIX_LAKE_URL`, a different injected var, is **never read** — don't confuse it with the `Quix__Lakehouse__Query__Url` / `QUIXLAKE_URL` pair.)
- `validate_env()` logs (doesn't raise) when any of these are missing; request-time guards surface clean errors.
- Two httpx clients, module-level (amortise TLS + pool): `main._lake_http` (60 s, for `/query`) and `partition_walker._http_client` (30 s, for the manifest).

## 2. Partitions → dropdowns (the catalog manifest path)

**Backend** `GET /api/sessions[?<partition filters>]` (`main.py list_sessions`)
→ `partition_walker._list_session_combinations(filters)`:

```
GET {CATALOG_URL}/namespaces/default/tables/{TABLE_NAME}/manifest
    Authorization: Bearer {CATALOG_TOKEN}
→ { "entries": [ { "partition_values": {environment, test_rig, experiment,
                   driver, track, carModel, session_id, lap}, … }, … ] }
```

- ONE call (~130 ms, size-independent) — the catalog has an indexed
  manifest_entries table. **Never tree-walk `/partitions` or full-scan the
  Parquet just to list sessions.**
- Dedupe each entry's `partition_values` by the `PARTITION_COLS` tuple
  (`environment, test_rig, experiment, driver, track, carModel, session_id`);
  collect distinct `lap` ints per combo. Returns `[{...cols, laps:[1,2,…]}, …]`.
- `filters` (query params) trim the result **client-side after dedupe** (catalog
  is fast enough that pushdown isn't worth it).
- Error mapping in `list_sessions`: httpx `HTTPStatusError` → **502** (`detail="Data lake returned <status> <reason>"`), `TimeoutException` → **504**, anything else → **500**.

**Frontend** loads this once per tab open into `appState.sessions` (`data.js fetchSessions` → `app.js`). `fetchSessions` parses the server `detail` into the error so the toast shows the real upstream status.

## 3. Dropdown cascade (no network) — `selections.js` + `state.js` + `data.js`

- `PART_COLS` (`state.js`) **mirrors** `partition_walker.PARTITION_COLS` (same 7
  columns, same order). `PART_LABELS` is the display text (Env/Rig/…/Session).
- Each row renders one `<select>` per column. Changing column *i* repopulates
  *i+1…n* via `populateDropdowns()`:
  - `getDistinctValues(col, upstream)` (`data.js`) filters `appState.sessions`
    by the upstream selections and returns sorted distinct values — **pure array
    work, zero HTTP.**
  - Single possible value → auto-selects (Test Manager pattern, no placeholder).
  - Laps are **baked into each session object** (`session.laps`) by `/api/sessions`,
    so `loadLaps()` needs no extra call.
- `getSelections()` gathers, per checked lap, `{ key: {…PART_COLS values}, lap, color, label }`. Multi-session selections get `S<n>-L<lap>` labels.

## 4. Telemetry data query (the DuckDB path)

**Frontend** `data.js fetchTelemetry(sel, signals)`:
```
GET /api/telemetry?<sel.key partition cols>&lap=<N>&signals=a,b,c
```

**Backend** `main.py get_telemetry`:
1. Validate each signal with `str.isidentifier()` → bad name = **400** (blocks SQL injection via the column list).
2. `where = _build_partition_filter(**partition_keys, lap=lap)` (`partition_filter.py`) — `ValueError` → **400**.
3. Build + run SQL:
   ```sql
   SELECT normalizedCarPosition, timestamp_ms, <signals> FROM {TABLE_NAME} {where}
   ```
   via `_lake_query(sql)`:
   ```python
   POST {QUIXLAKE_URL}/query
       Authorization: Bearer {QUIX_LAKE_TOKEN}
       Content-Type: text/plain        # body IS the raw SQL
   → CSV  → pd.read_csv(...)            # non-200 → HTTPException(502)
   ```
4. Post-process in pandas (not SQL): `sort_values("normalizedCarPosition")`;
   lap==1 start-line trim; `sanitize_df` (NaN/Inf→None). Return
   `{session_id, lap, signals, count, data: df.to_dict("list")}`.
5. `except HTTPException: raise` **first** (preserve the 502 from `_lake_query`);
   then 504 on timeout, 500 otherwise.

## 5. WHERE-clause builder — `partition_filter.py`

- `_build_partition_filter(**kwargs)` skips empty values; `int` → `col = N`
  (used for `lap`); strings validated against allowlist
  `_SAFE_PARTITION_VALUE = ^[A-Za-z0-9_\-.: ]+$` (SQL-injection guard — anything
  else raises `ValueError`).
- `session_id` is special: Hive stores `2026-04-14T11:42:08.107Z` but the
  frontend may send `2026-04-14 11:42:08.107000`. Builder normalises to a common
  prefix and emits `CAST(session_id AS VARCHAR) LIKE '<prefix>%' ESCAPE '\'`
  (LIKE metachars in the value are escaped).
- Other columns → `col = '<val>'`.

## 6. QuixLake query gotchas (DuckDB-backed)

- **No CTE / `WITH`** — silently returns 0 rows. Single-level `GROUP BY` + reduce in pandas.
- **Aggregations are slow** — `MIN`/`GROUP BY`/`FILTER` on derived tables time out (~30 s). Raw scan + pandas aggregation.
- **ORDER BY dropped on purpose** — sorting in pandas (~5 ms) beats DuckDB's cold/warm sort; the frontend `downsample()` requires data sorted on `normalizedCarPosition`.
- **Full scans can fail** if an orphan, non-partitioned Parquet sits at the table root (`Binder Error: Hive partition mismatch`). Partition-equality filters push to the S3 prefix and dodge it. The telemetry query is always partition-filtered; the manifest path avoids scans entirely.
- `iCurrentTime` is lap-relative (resets each lap); `timestamp_ms` is ingest wall-clock. Don't conflate.

## File map

| File | Role |
|---|---|
| `config.py` | URL/token resolution, httpx clients, `validate_env` |
| `main.py` | `_lake_query`, `/api/sessions`, `/api/telemetry`, error→HTTP mapping |
| `partition_walker.py` | catalog `/manifest` read, `PARTITION_COLS`, dedupe→sessions |
| `partition_filter.py` | `_build_partition_filter`, allowlist, session_id LIKE |
| `static/modules/state.js` | `PART_COLS`/`PART_LABELS`, `appState.sessions` |
| `static/modules/data.js` | `fetchSessions`, `fetchTelemetry`, `getDistinctValues` |
| `static/modules/selections.js` | dropdown cascade, lap pickers, `getSelections` |

Related: `track-geometry-mongo` (the map, Mongo path), `video-seeking` (marker↔video sync).
