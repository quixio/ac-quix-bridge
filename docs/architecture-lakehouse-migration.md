# Architecture: Lakehouse Migration (telemetry-comparison query API swap)

**Feature:** Lakehouse Migration — Pass A
**Date:** 2026-06-04
**Branch:** feature/lakehouse-migration

---

## What this code does

`telemetry-comparison` queries Hive-partitioned Parquet telemetry data via a remote SQL API. Previously this pointed at QuixLake (`POST /query`). This migration swaps the query endpoint to the Quix Lakehouse Query API (`POST /api/query?union_by_name=true`) and renames all Python config attributes from QuixLake names (`QUIXLAKE_URL`, `QUIX_LAKE_TOKEN`, `CATALOG_URL`, `CATALOG_TOKEN`) to Lakehouse names (`LAKEHOUSE_QUERY_URL`, `LAKEHOUSE_QUERY_TOKEN`, `LAKEHOUSE_CATALOG_URL`, `LAKEHOUSE_CATALOG_TOKEN`). The env vars the service reads are now exclusively `Quix__Lakehouse__*` — no legacy fallback reads. The `/api/telemetry` JSON response contract to the browser is unchanged.

Pass A covers: probe, config rename, comment updates, TimescaleDB audit, test/mock fixups, README update. Pass B covers: full KB rewrite, `kb_lakehouse_api.md` creation (probe-blocked items resolved).

---

## Why this architecture

**Minimal-change approach.** The transport layer (`httpx.AsyncClient`) is unchanged. The response format confirmed by probe is `text/csv` — `pd.read_csv` continues to work. Only URL construction and attribute names changed. This keeps the diff small and the risk surface narrow.

**Clean break on env vars.** QuixLake env var names (`QUIXLAKE_URL`, `QUIX_LAKE_TOKEN`, etc.) are no longer read. The fallback table that previously allowed both names was removed. Quix Cloud auto-injects `Quix__Lakehouse__*` vars on byox deployments; local `.env` files must now use those names. The README's deprecated-vars notice explains the cutover.

**Probe-first discipline.** Step 0 ran against the live demo deployment before any URL was changed in production code. Key findings that gate Steps 2 and 4 (response parser, error handling, KB rewrite):
- Response is `text/csv` — parser unchanged.
- SQL errors return HTTP 200 with a body starting `\n# ERROR: <message>` — the existing `status_code != 200` guard does NOT catch these. Step 2 must add a body-prefix check.
- Legacy `/query` path returns 405 — no rollback path available.
- `union_by_name=true` is optional (items 2 and 4 of probe return identical bytes) but passed unconditionally per spec.

---

## Data flows

### Query path (telemetry endpoint)

```
browser GET /api/telemetry?...
  → main.py /api/telemetry handler
    → _lake_query(sql)
      → httpx POST {LAKEHOUSE_QUERY_URL}/query   ← URL swap pending Step 2
        (currently still /query; /api/query swap is probe-blocked Step 2)
        headers: Authorization: Bearer {LAKEHOUSE_QUERY_TOKEN}
        body: raw SQL text
      ← text/csv response
    → pd.read_csv(io.StringIO(r.text))
    → sort by normalizedCarPosition, lap-1 trim, downsample
  → JSON {session_id, lap, signals, count, data}
```

Note: the URL path (`/query` → `/api/query`) is **not yet swapped** — that is Step 2, which is probe-blocked pending user review of error-handling strategy (HTTP 200 with `# ERROR:` body requires a new body-prefix check before the path change is safe).

### Sessions path (catalog endpoint)

```
browser GET /api/sessions?...
  → main.py /api/sessions handler
    → _list_session_combinations(filters)  [partition_walker.py]
      → httpx GET {LAKEHOUSE_CATALOG_URL}/namespaces/default/tables/{TABLE_NAME}/manifest
        headers: Authorization: Bearer {LAKEHOUSE_CATALOG_TOKEN}
      ← JSON {entries: [...]}
    → dedupe partition_values → [{session_id, ..., laps: [...]}]
  → JSON {sessions: [...]}
```

---

## File inventory

### Modified

| File | Change |
|---|---|
| `telemetry-comparison/config.py` | Renamed 4 attrs (`QUIXLAKE_URL` → `LAKEHOUSE_QUERY_URL`, etc.). Removed legacy `or os.getenv(...)` fallbacks. Updated `validate_env()` to use new env-var names. Updated docstring and agent warning comment. Added `BLOB_STORAGE_CONNECTION_JSON` comment-only entry. |
| `telemetry-comparison/main.py` | Updated `_lake_query` to reference `config.LAKEHOUSE_QUERY_URL` / `config.LAKEHOUSE_QUERY_TOKEN`. Updated docstring and `_lake_http` comment. URL path (`/query`) not yet changed — awaiting Step 2 review. |
| `telemetry-comparison/partition_walker.py` | Updated `_list_session_combinations` to reference `config.LAKEHOUSE_CATALOG_URL` / `config.LAKEHOUSE_CATALOG_TOKEN`. Updated module docstring. |
| `telemetry-comparison/chat.py` | Module docstring: `(QuixLake Querier)` → `(Lakehouse Querier)`. Comment at line 102: `"QuixLake guard"` → `"Lakehouse guard"`. |
| `telemetry-comparison/README.md` | Updated opening description, `chat.py` row in files table, 4 env-var rows in environment table, added deprecated-vars note, updated AI chat table. |
| `telemetry-comparison/tests/conftest.py` | Module docstring, `config_env` fixture (`QUIXLAKE_URL`/`QUIX_LAKE_TOKEN` → new attrs), `stub_lake` fixture docstring. |
| `telemetry-comparison/tests/test_telemetry.py` | 2 tests updated: attr names and error-message assertions. |
| `telemetry-comparison/tests/test_sessions.py` | All `config.CATALOG_URL` / `config.CATALOG_TOKEN` → `config.LAKEHOUSE_CATALOG_URL` / `config.LAKEHOUSE_CATALOG_TOKEN`. Error-message string assertions updated. |
| `dev-planning/lakehouse-migration/spec.md` | Appended `## Probe results` section with full stdout and Key findings. |

### Created

| File | Purpose |
|---|---|
| `telemetry-comparison/scripts/probe_lakehouse.py` | One-shot discovery script. Gitignored. Fires 6 HTTP calls against the live Lakehouse and prints labelled blocks for pasting into the spec. Uses `verify=False` for the demo deployment's self-signed cert. |

---

## Integration points

- **`config.py` → all consumers:** `main.py`, `partition_walker.py`, `chat.py`, and all test files read attrs off `config` at call time (not at import time). The rename propagates correctly because no file copies the value at module load.
- **Tests mock at the httpx transport level** (`httpx.MockTransport`), not at the URL path level. Renaming the URL path in Step 2 will not break any test — the mock intercepts all outbound requests regardless of URL.
- **`partition_walker.py` is NOT in spec §3 non-goals** — that section only excludes `partition_filter.py`, `track_loader.py`, `video_proxy.py`. `partition_walker.py` consumes `CATALOG_URL`/`CATALOG_TOKEN` directly, so it must be updated in the same pass as `config.py`. Not touching the actual catalog endpoint URL or logic.

---

## Probe findings summary (for Steps 2 and 4)

| Question | Answer |
|---|---|
| Q1: Response format? | `text/csv` — `pd.read_csv` unchanged |
| Q2: `union_by_name=true` required? | Optional; pass unconditionally |
| Q3: Error response shape? | HTTP **200** with body prefix `\n# ERROR: <DuckDB message>` — current guard misses this |
| Q4: Legacy `/query` path work? | **405 Not Allowed** — no rollback |
| Q5: CTEs supported? | Not determinable from probe; Step 4 / Pass B to confirm from swagger |
| Q6: Metadata endpoint paths? | `/api/tables` → JSON; `/api/schema?table=` → JSON. Both confirmed. |
| SSL | Self-signed cert on demo — `verify=False` in probe script only; production keeps default `verify=True` |

---

## Open items before Pass B

1. **Step 2 (main.py URL swap):** Change `/query` → `/api/query` and add body-prefix error check for `# ERROR:` (HTTP 200 SQL errors). Design decision needed: raise `HTTPException(502)` on `# ERROR:` prefix, or return empty DataFrame?
2. **Step 4 (KB rewrite):** `kb_quixlake_api.md` → `kb_lakehouse_api.md`. Confirm CTE support from swagger (Q5). Update `run_query` KB description to say "Returns CSV". Update `agent_system_prompt.md` QuixLake → Lakehouse references.
