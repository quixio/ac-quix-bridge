# QuixLake — Tool Reference

You access QuixLake through three MCP tools. The host backend brokers the actual HTTP — you never see URLs, tokens, or HTTP status codes. Each tool returns CSV text.

## Tools

### `run_query(sql: str) -> str`

Execute a SELECT against `ac_telemetry`. Returns CSV (first line = header, `NULL` rendered as empty field).

```
sql = "SELECT lap, MIN(iBestTime) AS best_ms FROM ac_telemetry "
      "WHERE environment='prague_office' AND driver='ludvik' GROUP BY lap"
```

### `get_schema(table: str) -> str`

Return column schema for `table` as CSV: `name,type,nullable,is_partition`. Lightweight (does not scan data).

Call only when a column name isn't in the channels KB or a query fails with an unknown-column error. For `ac_telemetry` the channels KB is authoritative; routine schema calls are waste.

### `list_partitions(table: str, path: str = "") -> str`

Return one level of the partition tree under `path`. CSV: `name,has_children`.

`path` is a Hive-style prefix following the partition column order:
`environment / test_rig / experiment / driver / track / carModel / session_id / lap`.

Empty string returns the top level. To go straight to session_ids for a known driver+car+track, pass the deepest known prefix in one call:

```
list_partitions(
  table="ac_telemetry",
  path="environment=prague_office/test_rig=g29/experiment=VideoSyncFix/"
       "driver=ludvik/track=ks_nurburgring/carModel=bmw_1m"
)
# → name,has_children
#   session_id=2026-04-17T06:35:49.976Z,true
#   session_id=2026-04-17T06:39:45.652Z,true
```

Use only when the sessions KB misses or a query returns 0 rows. Walking from root takes 7+ sequential calls — prefer the deepest prefix you can build from KB context.

## Query rules (hard — violating these makes queries wrong or slow)

1. **Partition-filter every SELECT.** Every `SELECT` must include `WHERE <partition_column> = '...'` for at least one partition column. Full-scan queries defeat manifest-first planning. See the dataset KB for which columns are partition columns.
2. **Never hallucinate column names.** If unsure, call `get_schema`. Unknown column references fail with a cryptic DuckDB error, not a clean message.
3. **Always `LIMIT` exploratory queries.** Use `LIMIT 100` until you know the result-set size.
4. **Project only needed columns — never `SELECT *` by default.** CSV serialisation of wide tables dominates latency. For a 180-column table, `SELECT *` for one session (~20k rows) takes 15-22 s; projecting 3 columns returns in <1 s.
5. **Only plain `SELECT` is accepted.** `WITH` / CTE, DDL, DML all return `only SELECT allowed`. Use subqueries instead of CTEs.

## Latency expectations

- `get_schema`: ~100-300 ms
- `list_partitions` one level: ~200-500 ms
- `run_query` warm, narrow partition-filtered SELECT: ~500 ms
- `run_query` with `GROUP BY` or wide projection: 3-10 s
- `run_query` with `SELECT *` for a single session: 15-22 s (CSV serialisation)

If a query takes >10 s with a narrow projection, the WHERE clause probably isn't partition-filtering correctly. Tighten and retry.
