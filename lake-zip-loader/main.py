"""lake-zip-loader — HTTP -> Quix Lakehouse parquet loader.

Receives already-final-format, Hive-partitioned parquet files over HTTP and
sinks them into a Quix Lakehouse table: writes the bytes to blob storage under
the standard time-series layout and registers each file in the Iceberg REST
catalog manifest. It intentionally has NO Kafka topics — the parquet is already
partition-dropped final format, so re-streaming it through the broker would be a
pointless round trip. It replicates the write mechanics of
``quixstreams.sinks.core.QuixTSDataLakeSink`` (blob key layout, catalog table
registration, per-file ``add-files``) for a batch/backfill push path.

Storage key layout (workspace id is prepended automatically by the blob client
because it is passed as ``base_path``)::

    data-lake/time-series/{TABLE_NAME}/{col1=val1}/.../data_<hex>.parquet

Credentials are read ONLY from the platform's auto-injected ``Quix__*`` env
vars (never a PAT). ``Quix__BlobStorage__Connection__Json`` is consumed
implicitly by quixportal via ``blobStorage: bind: true`` on the deployment.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from quixstreams.sinks.core._blob_storage_client import (
    BlobStorageClient,
    get_bucket_name,
)
from starlette.concurrency import run_in_threadpool

from catalog import CatalogManager

logging.basicConfig(
    level=os.getenv("LOGLEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("lake-zip-loader")

# --- Static configuration ---------------------------------------------------
# Path prefix all time-series tables live under (matches QuixTSDataLakeSink).
TIMESERIES_PREFIX = "data-lake/time-series"
TABLE_NAME = os.getenv("TABLE_NAME", "ac_telemetry_prod_test")
CATALOG_NAMESPACE = os.getenv("CATALOG_NAMESPACE", "default")
# Fixed partition scheme of the target table, order-significant for validation.
EXPECTED_PARTITIONS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
    "lap",
]
MAX_WRITE_WORKERS = int(os.getenv("MAX_WRITE_WORKERS", "8"))
# Cap for the /status file count scan (well above the ~371 of a single export).
STATUS_LIST_LIMIT = 100_000


class _State:
    """Process-wide handles initialised once on startup."""

    def __init__(self) -> None:
        self.blob_client: BlobStorageClient | None = None
        self.bucket: str | None = None
        self.catalog: CatalogManager | None = None
        self.workspace_id: str = ""


state = _State()


def _init_backends() -> None:
    """Wire up blob storage and (optionally) the Iceberg catalog.

    Blob failures are fatal (the service is useless without storage). Missing
    catalog credentials degrade to blob-only with a loud warning; a catalog
    that is reachable but fails table registration is logged and left active so
    per-file writes can retry (see ``CatalogManager.register_file``).
    """
    workspace_id = os.getenv("Quix__Workspace__Id", "")
    state.workspace_id = workspace_id

    blob = BlobStorageClient(base_path=workspace_id, max_workers=MAX_WRITE_WORKERS)
    blob.ensure_path_exists(auto_create=True)
    state.blob_client = blob
    state.bucket = get_bucket_name()
    logger.info(
        "Blob storage ready: s3://%s/%s/%s/%s",
        state.bucket,
        workspace_id or "<no-workspace>",
        TIMESERIES_PREFIX,
        TABLE_NAME,
    )

    catalog_url = os.getenv("Quix__Lakehouse__Catalog__Url") or os.getenv("CATALOG_URL")
    catalog_token = os.getenv("Quix__Lakehouse__Catalog__AuthToken")
    if not catalog_url:
        logger.warning(
            "Quix__Lakehouse__Catalog__Url is NOT set — running BLOB-ONLY. Files "
            "will be written to storage but NOT registered in the Iceberg catalog, "
            "so they will be invisible to lakehouse queries until registered."
        )
        state.catalog = None
        return

    catalog = CatalogManager(
        url=catalog_url,
        auth_token=catalog_token,
        namespace=CATALOG_NAMESPACE,
        table_name=TABLE_NAME,
        bucket=state.bucket,
        workspace_id=workspace_id,
        s3_prefix=TIMESERIES_PREFIX,
        expected_partitions=EXPECTED_PARTITIONS,
    )
    try:
        catalog.ensure_table_registered()
    except Exception:  # noqa: BLE001 - keep the service up for diagnosis
        logger.exception(
            "Catalog table registration failed at startup; keeping catalog active "
            "so per-file writes can retry. Check catalog URL/token if writes 502."
        )
    state.catalog = catalog


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_backends()
    yield
    if state.blob_client is not None:
        state.blob_client.shutdown()


app = FastAPI(title="lake-zip-loader", lifespan=lifespan)


# --- Helpers ----------------------------------------------------------------


def _check_api_key(provided: str | None) -> None:
    """Fail closed: reject unless the header matches the configured secret."""
    expected = os.getenv("UPLOAD_API_KEY")
    if not expected or provided != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-Api-Key")


def _validate_relpath(relpath: str) -> tuple[dict[str, str], str]:
    """Validate a Hive-partitioned relative path and extract partition values.

    Enforces: no traversal, exactly the expected partition columns in the exact
    expected order, each as ``col=value`` with a non-empty value, and a
    ``.parquet`` filename. Returns ``(partition_values, normalised_relpath)``.
    """
    segments = [s for s in relpath.strip("/").split("/") if s]
    if ".." in segments or any("\\" in s for s in segments):
        raise HTTPException(status_code=400, detail="illegal path segment")
    if len(segments) != len(EXPECTED_PARTITIONS) + 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"expected {len(EXPECTED_PARTITIONS)} partition segments + a "
                f"filename, got {len(segments)} segment(s)"
            ),
        )
    filename = segments[-1]
    if not filename.endswith(".parquet"):
        raise HTTPException(status_code=400, detail="filename must end with .parquet")

    partition_values: dict[str, str] = {}
    for segment, expected_col in zip(segments[:-1], EXPECTED_PARTITIONS):
        col, sep, val = segment.partition("=")
        if sep != "=" or col != expected_col or val == "":
            raise HTTPException(
                status_code=400,
                detail=f"segment '{segment}' must be '{expected_col}=<value>'",
            )
        partition_values[col] = val

    return partition_values, "/".join(segments)


def _store_file(relpath: str, body: bytes) -> dict:
    """Idempotently store one parquet file and register it in the catalog.

    Runs in a worker thread (all I/O here is blocking). If catalog registration
    fails after the blob write, the orphan object is rolled back so that
    "object exists in blob" always implies "fully processed" — which is what the
    idempotency short-circuit relies on for safe re-runs.
    """
    if state.blob_client is None:
        raise HTTPException(status_code=503, detail="blob storage not initialised")

    partition_values, clean_relpath = _validate_relpath(relpath)
    storage_key = f"{TIMESERIES_PREFIX}/{TABLE_NAME}/{clean_relpath}"

    try:
        already_present = state.blob_client.exists(storage_key)
    except Exception as exc:  # noqa: BLE001 - e.g. SignatureDoesNotMatch on non-ASCII keys
        logger.warning(
            "exists check failed for %s (%s) — assuming absent", storage_key, exc
        )
        already_present = False
    if already_present:
        logger.info("Skip existing object: %s", storage_key)
        return {"status": "exists", "key": clean_relpath}

    if not body:
        raise HTTPException(status_code=400, detail="empty request body")

    try:
        row_count = pq.read_metadata(pa.BufferReader(body)).num_rows
    except Exception as exc:  # noqa: BLE001 - surface as a 400 to the uploader
        raise HTTPException(
            status_code=400, detail=f"not a readable parquet file: {exc}"
        ) from exc

    try:
        state.blob_client.put_object(storage_key, body)
    except Exception as exc:  # noqa: BLE001 - surface the real storage error as 502
        raise HTTPException(
            status_code=502, detail=f"blob write failed: {exc}"
        ) from exc

    if state.catalog is not None:
        try:
            state.catalog.register_file(
                storage_key, len(body), partition_values, row_count
            )
        except Exception as exc:  # noqa: BLE001 - roll back then report
            try:
                state.blob_client.delete_object(storage_key)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.exception("Rollback of orphan blob failed: %s", storage_key)
            raise HTTPException(
                status_code=502, detail=f"catalog registration failed: {exc}"
            ) from exc

    logger.info("Stored %s (%d rows, %d bytes)", storage_key, row_count, len(body))
    return {"status": "stored", "rows": row_count, "bytes": len(body)}


# --- Routes -----------------------------------------------------------------


@app.put("/files/{relpath:path}")
async def put_file(
    relpath: str,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> dict:
    """Store one Hive-partitioned parquet file. Body = raw parquet bytes."""
    _check_api_key(x_api_key)
    body = await request.body()
    return await run_in_threadpool(_store_file, relpath, body)


@app.get("/status")
def status() -> dict:
    """Report the configured table, parquet file count, and catalog state."""
    if state.blob_client is None:
        raise HTTPException(status_code=503, detail="blob storage not initialised")
    table_prefix = f"{TIMESERIES_PREFIX}/{TABLE_NAME}/"
    objects = state.blob_client.list_objects(
        prefix=table_prefix, max_keys=STATUS_LIST_LIMIT
    )
    parquet_files = sum(1 for obj in objects if str(obj["Key"]).endswith(".parquet"))
    return {
        "table": TABLE_NAME,
        "files": parquet_files,
        "catalog": state.catalog is not None,
    }


@app.get("/verify")
def verify():
    """Run ``SELECT count(*)`` against the lakehouse query API and echo the CSV.

    A 0-row / empty result can be transient on a cold cache; the raw response is
    returned as-is (no server-side retry) so the caller can decide.
    """
    query_url = os.getenv("Quix__Lakehouse__Query__Url")
    query_token = os.getenv("Quix__Lakehouse__Query__AuthToken")
    if not query_url:
        return JSONResponse(
            status_code=503,
            content={"error": "Quix__Lakehouse__Query__Url is not set"},
        )

    sql = f"SELECT count(*) FROM {TABLE_NAME}"
    headers = {"Content-Type": "text/plain"}
    if query_token:
        headers["Authorization"] = f"Bearer {query_token}"

    try:
        with httpx.Client(verify=False, timeout=60) as client:
            resp = client.post(
                f"{query_url.rstrip('/')}/query", content=sql, headers=headers
            )
    except Exception as exc:  # noqa: BLE001 - report the transport error
        return JSONResponse(status_code=502, content={"error": str(exc)})

    return PlainTextResponse(content=resp.text, status_code=resp.status_code)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
