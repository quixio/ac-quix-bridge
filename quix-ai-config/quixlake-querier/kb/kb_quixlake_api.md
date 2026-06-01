# QuixLake — Tool Reference

You access QuixLake through five MCP tools. The host backend brokers the actual HTTP — you never see URLs, tokens, or HTTP status codes. Each tool returns CSV text.

The default table is `ac_telemetry_leadboard` (current sink target — all sessions recorded after 2026-05-29 land here). The legacy `ac_telemetry` table holds older sessions, is read-only, and has a partially broken Hive layout — only partition-filtered queries succeed against it.

**Table-fallback flow:** call `list_session_combinations` on `ac_telemetry_leadboard` first. If the user's session isn't there, retry on `ac_telemetry`. If still absent, call `list_tables` and try any other table whose name starts with `ac_telemetry`. Stick with the table that has the session for the rest of the conversation.

## Tools

### `list_session_combinations(table: str) -> str`

Return every distinct session-level partition combination for `table` as CSV: `environment,test_rig,experiment,driver,track,carModel,session_id,laps`. The `laps` column lists recorded lap numbers as a semicolon-joined sorted string (e.g. `1;2;3`).

```
list_session_combinations(table="ac_telemetry_leadboard")
# → environment,test_rig,experiment,driver,track,carModel,session_id,laps
#   prague_office,g29,LeaderBoard,tomas,ks_nurburgring,bmw_1m,2026-05-29T09:39:06.113Z,1;2;3;4;5
#   prague_office,g29,LeaderBoard,ludvik,ks_nurburgring,bmw_1m,2026-05-28T12:35:32.325Z,1;2;3
#   ...
```

**Use this for every session-enumeration need.** Prefer it over `SELECT DISTINCT` for two reasons:

1. **~20× faster** — single Postgres-backed catalog query, ~150–250 ms. `SELECT DISTINCT` on the lake takes ~3 s for the same data.
2. **No `session_id` format trap.** The returned `session_id` is the raw ISO+`Z` string from the Hive path (e.g. `2026-05-29T09:39:06.113Z`). It is safe to feed straight into `WHERE session_id = '...'` without any conversion.

`session_id` is NOT a unique key on its own. The same ISO timestamp can appear across multiple `(driver, experiment)` combinations. Always combine `session_id` with at least `driver` and `experiment` in WHERE clauses.

### `list_tables() -> str`

Return every table registered in QuixLake as CSV: `name,file_count,size_mb`. Use when the user references a table you don't recognise, or when `list_session_combinations` on both known AC tables returns nothing useful.

The current AC tables are `ac_telemetry_leadboard` (active sink) and `ac_telemetry` (legacy, broken layout). Any other table whose name starts with `ac_telemetry` is also AC telemetry data and should be tried as a fallback. Tables like `carcolours_*`, `temperature`, `todata` are unrelated to AC telemetry — ignore them.

### `run_query(sql: str) -> str`

Execute a SELECT against any table. Returns CSV (first line = header, `NULL` rendered as empty field).

```
sql = "SELECT lap, MIN(iBestTime) AS best_ms FROM ac_telemetry_leadboard "
      "WHERE environment='prague_office' AND driver='ludvik' GROUP BY lap"
```

### `get_schema(table: str) -> str`

Return column schema for `table` as CSV: `name,type,nullable,is_partition`. Lightweight (does not scan data).

Call only when a column name isn't in the channels KB or a query fails with an unknown-column error. For the AC tables the channels KB is authoritative; routine schema calls are waste.

### `list_partitions(table: str, path: str = "") -> str`

Return one level of the partition tree under `path`. CSV: `name,has_children`.

`path` is a Hive-style prefix following the partition column order:
`environment / test_rig / experiment / driver / track / carModel / session_id / lap`.

Use only as a last-resort escape hatch — `list_session_combinations` is the bulk session-enumeration tool. `list_partitions` is appropriate when:
- You need to enumerate `lap` values for a specific session and the `laps` column from `list_session_combinations` is insufficient (rare).
- `list_session_combinations` returned no matching session AND the user insists the data exists at a deeper-than-cached level.

## Query rules (hard — violating these makes queries wrong or slow)

1. **Partition-filter every SELECT.** Every `SELECT` must include `WHERE <partition_column> = '...'` for at least one partition column. Full-scan queries defeat manifest-first planning. See the dataset KB for which columns are partition columns.
2. **Combine `session_id` with `driver` and `experiment` in WHERE.** `session_id` alone is not unique — the same ISO timestamp can appear across different drivers and experiments.
3. **Never hallucinate column names.** If unsure, call `get_schema`. Unknown column references fail with a cryptic DuckDB error, not a clean message.
4. **Always `LIMIT` exploratory queries.** Use `LIMIT 100` until you know the result-set size.
5. **Project only needed columns — never `SELECT *` by default.** CSV serialisation of wide tables dominates latency. For a 180-column table, `SELECT *` for one session (~20k rows) takes 15–22 s; projecting 3 columns returns in <1 s.
6. **Only plain `SELECT` is accepted.** `WITH` / CTE, DDL, DML all return `only SELECT allowed`. Use subqueries instead of CTEs.
7. **Use `list_session_combinations` instead of `SELECT DISTINCT` for session enumeration.** ~20× faster, and avoids the `session_id` TIMESTAMP display-vs-WHERE trap that bites raw SELECT DISTINCT (DuckDB renders `session_id` as `2026-04-14 14:06:59.113000` but `WHERE` only matches the ISO+`Z` form).

## Latency expectations

- `list_tables`: ~100–300 ms
- `list_session_combinations`: ~150–250 ms
- `get_schema`: ~100–300 ms
- `list_partitions` one level: ~200–500 ms
- `run_query` warm, narrow partition-filtered SELECT: ~500 ms
- `run_query` with `GROUP BY` or wide projection: 3–10 s
- `run_query` with `SELECT *` for a single session: 15–22 s (CSV serialisation)

If a query takes >10 s with a narrow projection, the WHERE clause probably isn't partition-filtering correctly. Tighten and retry.
