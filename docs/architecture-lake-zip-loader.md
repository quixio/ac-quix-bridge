# Architecture — lake-zip-loader (HTTP → Lakehouse parquet backfill)

## What this is

A batch/backfill loader that ingests already-final-format, Hive-partitioned
parquet files into a Quix Lakehouse table over HTTP — **no Kafka**. A FastAPI
service (`lake-zip-loader/`) accepts one parquet file per `PUT /files/{relpath}`
request, writes the bytes to blob storage under the canonical time-series layout,
and registers each file in the Iceberg REST catalog manifest. A companion CLI
(`scripts/upload_lake_zip.py`) streams every `*.parquet` entry of an export zip
to the service concurrently.

It exists because a telemetry export zip (371 files, ~1.4 GB) contains parquet
that is *already* the sink's output format — partition columns dropped, Hive
paths baked in. The idiomatic ingest (`ac-telemetry-lake`'s `QuixTSDataLakeSink`)
consumes Kafka and re-partitions; pushing this pre-partitioned data back through
the broker would be a pointless round trip. So this service reuses the sink's
**write mechanics** (blob key layout, catalog table registration, per-file
`add-files`) without its **transport** (Kafka/SDF batching).

The three outermost partitions (`environment`/`test_rig`/`experiment`) are NOT in
the zip entry paths — they are encoded in the zip *filename*. The uploader
derives them into a path prefix; the service treats the full 8-segment path
uniformly.

Target table default: `ac_telemetry_prod_test`. Built to the ArchDev brief on
branch `leadboard-sp`.

## Why this design

- **Reuse the sink's private clients, not a reimplementation.** The blob key
  layout (`data-lake/time-series/{table}/{col=val}/…/data_*.parquet`, workspace
  id prepended by the client), the catalog table-registration payload, and the
  manifest `add-files` shape must match `QuixTSDataLakeSink` byte-for-byte or the
  Telemetry Explorer / leaderboard readers won't see the data. Rather than copy
  those constants, the service imports the same
  `quixstreams.sinks.core._blob_storage_client.BlobStorageClient` /
  `get_bucket_name` and `_quix_ts_datalake_catalog_client.QuixTSDataLakeCatalogClient`
  the sink uses. These are underscore-private modules; that coupling is
  deliberate and is the whole point — it keeps write semantics identical. If a
  future quixstreams bump renames them, this service and the sink break together
  (good — they must stay in lockstep).
- **HTTP PUT-per-file, not a bulk upload.** One file per request keeps each unit
  independently retryable and idempotent, bounds server memory to one file at a
  time per worker, and lets the CLI parallelize with a plain thread pool. The
  file's Hive path is the URL path, so the request is self-describing.
- **Idempotency via blob existence, with rollback to preserve the invariant.**
  `PUT` short-circuits to `{"status":"exists"}` when the object is already in
  blob — that is what makes re-running a partially-failed upload cheap and safe.
  For that short-circuit to be *correct*, "object exists in blob" must imply
  "fully processed (blob + manifest)". So if catalog registration fails after the
  blob write, the orphan object is deleted before returning 502. The one gap:
  a process kill *between* blob write and manifest post leaves an unregistered
  orphan that a re-run will skip — surfaced by `/verify` count < `/status` count
  (see Integration).
- **Credentials are auto-injected `Quix__*` env vars only — never a PAT.** Blob
  auth rides `Quix__BlobStorage__Connection__Json` (consumed implicitly by
  quixportal via `blobStorage: bind: true`, never parsed here). Catalog and query
  use `Quix__Lakehouse__Catalog__{Url,AuthToken}` and
  `Quix__Lakehouse__Query__{Url,AuthToken}`. This is a hard project rule
  (`MEMORY.md`: "NEVER PAT for lakehouse"); there is no PAT fallback anywhere.
- **Blob-fatal, catalog-degradable startup.** No blob = the service is useless,
  so blob init failure crashes (restart loop until config is right, matching the
  sink's `setup()`). Missing catalog vars → loud warning, blob-only. Catalog
  reachable but table-registration fails → logged, catalog kept active so
  per-file writes retry registration (`CatalogManager.register_file` self-heals a
  transient startup failure). This keeps `/status` and `/verify` available for
  diagnosis instead of a crash loop.
- **Sync work off the event loop.** `PUT`/`GET /status`/`GET /verify` do blocking
  I/O (blob, pyarrow, `requests`/`httpx`). The route handlers read the body async
  then hand the blocking work to `run_in_threadpool` (PUT) or run as sync `def`
  (Starlette threadpools them), so N concurrent uploads don't stall the loop.
- **Fixed partition scheme, validated server-side.** `EXPECTED_PARTITIONS` is a
  module constant (the target table's fixed 8-column scheme), not env-driven. The
  `PUT` path is validated segment-by-segment in exact order, rejecting traversal
  and malformed `col=value` — a malformed backfill path is a hard 400, not a
  silently-misplaced file.

## Data flow

```
export.zip (filename encodes environment/test_rig/experiment)
   │  scripts/upload_lake_zip.py  (thread pool, --workers)
   │    • derive_prefix(filename) → environment=…/test_rig=…/experiment=…
   │    • per *.parquet entry: relpath = prefix + "/" + entry_path (8 partitions + file)
   │    • URL-encode (spaces/colons; keep "/" and "=") ; 3 retries, linear backoff
   ▼
PUT /files/{relpath:path}   (X-Api-Key: UPLOAD_API_KEY)     [lake-zip-loader]
   │  _check_api_key  → 401 if header ≠ env (fail-closed)
   │  _validate_relpath → partition_values{}, normalised relpath   (400 on bad shape)
   │  storage_key = data-lake/time-series/{TABLE_NAME}/{relpath}
   ├─ blob_client.exists(storage_key)? ── yes ─► 200 {"status":"exists"}   (no write)
   │        no
   ├─ row_count = pyarrow.parquet.read_metadata(BufferReader(body)).num_rows  (400 if unreadable)
   ├─ blob_client.put_object(storage_key, body)          → s3://{bucket}/{workspaceId}/{storage_key}
   └─ catalog.register_file(...)                         → POST …/manifest/add-files
             on failure: delete_object(storage_key) then 502
        ▼
   200 {"status":"stored","rows":N,"bytes":M}

Startup (lifespan): BlobStorageClient(base_path=workspaceId) → ensure_path_exists
                    → get_bucket_name() → CatalogManager.ensure_table_registered()
                    (GET table; PUT-create with empty partition_spec if 404)

GET /status  → list_objects(table prefix) → {table, files:<parquet count>, catalog:<bool>}   (no auth)
GET /verify  → POST "SELECT count(*) FROM {TABLE_NAME}" to {Query__Url}/query (Bearer, verify=False)
               → raw CSV text + upstream status                                                (no auth)
GET /healthz → {"status":"ok"}
```

Full S3 URIs (built in `catalog.py`, workspace-scoped):
- table location: `s3://{bucket}/{workspaceId}/data-lake/time-series/{table}`
- file path: `s3://{bucket}/{workspaceId}/{storage_key}`

## File inventory

Created:
- `lake-zip-loader/main.py` — FastAPI app: config constants (incl. fixed
  `EXPECTED_PARTITIONS`), `lifespan` backend init, `_check_api_key`,
  `_validate_relpath`, `_store_file`, and the four routes. ~270 lines.
- `lake-zip-loader/catalog.py` — `CatalogManager`: thread-safe
  `ensure_table_registered` (GET→PUT) and `register_file` (POST add-files); owns
  all bucket/workspace S3-URI construction. Wraps the sink's catalog client.
- `lake-zip-loader/requirements.txt` — mirrors `ac-telemetry-lake`
  (quixstreams git pin `eadff0e8…`, pandas, pyarrow, python-dotenv, the private
  `--extra-index-url` Azure DevOps feed + `quixportal[all]>=2.0.1`) plus
  `fastapi`, `uvicorn[standard]`, `httpx`. The pin and the extra-index-url are
  load-bearing: `quixportal` only resolves from that private feed.
- `lake-zip-loader/dockerfile` — `ac-telemetry-lake`'s dockerfile
  (python:3.13-slim-bookworm, `git` for the git+ dep, cached requirements
  install) with the entrypoint changed to `uvicorn main:app --host 0.0.0.0
  --port 80`.
- `lake-zip-loader/app.yaml` — `name: lake-zip-loader`, `language: python`, vars
  `TABLE_NAME` / `UPLOAD_API_KEY` / `LOGLEVEL`. No input/output topics.
- `scripts/upload_lake_zip.py` — CLI uploader (stdlib + httpx). `derive_prefix`,
  `_put_entry` (per-entry zip handle = thread-safe read; retries/backoff), thread
  pool over entries, per-file progress + final summary, non-zero exit on any
  failure.
- `docs/architecture-lake-zip-loader.md` — this doc.

Modified:
- `quix.yaml` — appended one deployment ("Lake Zip Loader", group `Ingestion`,
  Service, cpu 500 / mem 1500, replicas 1, publicAccess `lake-zip-loader`,
  port 80→80, `TABLE_NAME`/`UPLOAD_API_KEY`, `blobStorage: bind: true`).
  Append-only; no existing block, topic, or field touched.
- `scripts/gen_repo_index.py` — added `"lake-zip-loader": "fastapi"` to
  `PYTHON_SERVICES` so the new top-level service is actually indexed (otherwise
  regenerating the index is a no-op for it). `.claude/repo-index.json`
  regenerated (11 services).

## Integration with neighboring features

- **`ac-telemetry-lake` (`QuixTSDataLakeSink`)** — the write-mechanics reference.
  This loader is the non-Kafka twin: identical blob layout and catalog calls, so
  files it writes are indistinguishable from sink output. Both must resolve the
  **same table name per environment**; the sink's table is the per-env
  `AC_TELEMETRY_TABLE_NAME` project variable, whereas this loader defaults to
  `ac_telemetry_prod_test` (a *test* table by design — point it at the real table
  only intentionally). Its partition scheme matches the sink's `HIVE_COLUMNS`
  exactly: `environment,test_rig,experiment,driver,track,carModel,session_id,lap`.
- **Telemetry Explorer / leaderboard / dashboard** — all read the lake via
  QuixLake queries against a table name. A file that is in blob but *not* in the
  catalog manifest is invisible to them. `GET /status` (blob file count) vs
  `GET /verify` (`SELECT count(*)` via the query API, which reads the manifest) is
  the operator's cross-check: matching counts ⇒ every stored file is registered;
  `/status > /verify` ⇒ orphans to investigate (e.g. a mid-file kill, or a
  cold-cache transient on `/verify` — re-run it).
- **Blob binding** — `blobStorage: bind: true` on the deployment injects
  `Quix__BlobStorage__Connection__Json`, exactly as `ac-telemetry-lake`,
  `telemetry-comparison`, and `best-laps-lite` rely on it. Catalog/query URLs are
  auto-injected on dev/byox (`MEMORY.md`: byox auto-injects the Lakehouse Query
  vars); do not declare them in `app.yaml`.
- **`REPO_INDEX.md`** is hand-written and not touched by the generator; add a
  `lake-zip-loader` row there manually if the top-level map is being curated.
