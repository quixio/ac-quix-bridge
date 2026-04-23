# QuixLake API Reference

QuixLake is a REST service that queries Hive-partitioned Parquet on blob storage via an Iceberg catalog. Use this reference when reading data from a QuixLake deployment.

## Auth + base URL

- Base URL for the current deployment: `https://quixlake-quixdev-quixlakev2-dev.deployments-dev.quix.io`
- Every request sends `Authorization: Bearer $Quix__Sdk__Token`. The same SDK token injected into Quix Cloud workspace services works against any QuixLake deployment in the same organisation — token scope is org-level.
- `GET /health` returns 200 when the service is up and the token is valid. Use as a first probe when something fails.

## Endpoints (only three you need)

### `GET /schema?table={name}`

Returns column names + types. Lightweight, doesn't scan data.

```json
{"columns": [{"name": "speedKmh", "type": "double", "nullable": true}, ...]}
```

**Always call before composing a SELECT on an unknown table.** Column names can't be guessed — they follow the original data source's conventions. ~100-300 ms warm.

### `GET /partitions?table={name}&path={hive-path}` — escape hatch only

Returns one level of the partition tree. Lazy: start with no `path`, drill by appending `col=value` segments.

**Do NOT walk the full tree at runtime.** For "what sessions / drivers / tracks exist," use the sessions KB instead. Use `/partitions` only to:
- Verify a specific path exists before composing an expensive query
- Discover a new environment the sessions KB doesn't cover yet

### `POST /query`

The main data-read endpoint.

- Body: raw SQL as **plain text** (not JSON-wrapped)
- Header: `Content-Type: text/plain`
- Response: **CSV by default** — parse with `pd.read_csv(io.StringIO(r.text))`
- Optional params: `?explain=true` (plan + data as JSON), `?union_by_name=true` (tolerate schema drift, 3× slower — avoid)

```python
r = requests.post(
    f"{os.environ['QUIXLAKE_URL']}/query",
    data=sql,
    headers={
        "Authorization": f"Bearer {os.environ['Quix__Sdk__Token']}",
        "Content-Type": "text/plain",
    },
    timeout=60,
)
r.raise_for_status()
df = pd.read_csv(io.StringIO(r.text))
```

CSV notes: `NULL` renders as empty field (pandas → `NaN`). `timestamp` columns come through as ISO 8601 strings.

## DevSession execution pattern

When running queries from a Quix AI DevSession (spawned via `delegate_task`), you are inside an ephemeral Docker container with stdlib-only Python. The `/tmp/v` path below is inside that container — it has no host dependencies and dies with the session.

Install `requests` into a throwaway venv. Don't install globally (PEP 668 blocks it and `--break-system-packages` is a smell). Don't install pandas unless you actually need dataframe operations — analysis-mode responses are usually pre-aggregated in SQL, and stdlib `csv` parses the result in one line.

```bash
# Idempotent — creates venv only if missing; pip is a no-op if already installed.
# DevSession containers persist across delegate_task calls within a chat session,
# so subsequent queries skip the 5 s setup and go straight to the script.
[ -d /tmp/v ] || python3 -m venv /tmp/v
/tmp/v/bin/pip install -q requests
/tmp/v/bin/python - << 'PY'
import os, io, csv, requests

URL = "https://quixlake-quixdev-quixlakev2-dev.deployments-dev.quix.io"
TOKEN = os.environ["Quix__Sdk__Token"]

sql = "SELECT ... FROM ac_telemetry WHERE environment='...' LIMIT 100"
r = requests.post(f"{URL}/query", data=sql,
                  headers={"Authorization": f"Bearer {TOKEN}",
                           "Content-Type": "text/plain"},
                  timeout=60)
r.raise_for_status()
rows = list(csv.DictReader(io.StringIO(r.text)))
PY
```

Use `/tmp/v/bin/pip` and `/tmp/v/bin/python` directly — don't `source` inside heredocs.

Call `GET /schema?table=<name>` only when a column name isn't in the channels KB or the table is unfamiliar. For `ac_telemetry` the channels KB is authoritative; a schema call per query is waste.

## Query rules (hard — violating these makes queries wrong or slow)

1. **Partition-filter every SELECT.** Every `SELECT` must include `WHERE <partition_column> = '...'` for at least one partition column. Full-scan queries defeat the manifest-first planning. See the dataset KB for which columns are partition columns.
2. **Never hallucinate column names.** If unsure, call `/schema`. Unknown column references fail with a cryptic DuckDB error, not a clean 400.
3. **Always `LIMIT` exploratory queries.** Use `LIMIT 100` until you know the result-set size.
4. **Project only needed columns — never `SELECT *` by default.** CSV serialisation of wide tables dominates latency. For a 180-column table, `SELECT *` for one session (~20k rows) takes 15-22 s; projecting 3 columns returns in <1 s.
5. **Don't use `union_by_name=true` unless schemas actually differ across files.** 3× slower.
6. **Only plain `SELECT` is accepted.** `WITH` / CTE, DDL, DML all return `only SELECT allowed`. Use subqueries instead of CTEs.

## Latency expectations

- `/schema`, `/tables`, `/health`: ~100-300 ms
- `/query` warm, narrow partition-filtered SELECT: ~500 ms
- `/query` with `GROUP BY` or wide projection: 3-10 s
- `/query` with `SELECT *` for a single session: 15-22 s (CSV serialisation)

If a query takes >10 s with a narrow projection, you're probably not partition-filtering correctly. Check the WHERE clause.
