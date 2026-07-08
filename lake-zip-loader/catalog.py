"""Iceberg REST catalog helper for the lake-zip-loader service.

Thin wrapper around ``QuixTSDataLakeCatalogClient`` (the same client the
``QuixTSDataLakeSink`` uses) that replicates the sink's table-registration and
per-file manifest mechanics for the HTTP-push loader — i.e. without a Kafka
batch context. Two operations are exposed:

* :meth:`CatalogManager.ensure_table_registered` — GET the table, PUT-create it
  if absent (empty ``partition_spec``; partitions discovered on first write).
* :meth:`CatalogManager.register_file` — POST one file to the table manifest.

All full-S3-URI construction (bucket + workspace prefix) lives here so callers
only ever deal with workspace-relative storage keys.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from quixstreams.sinks.core._quix_ts_datalake_catalog_client import (
    QuixTSDataLakeCatalogClient,
)

logger = logging.getLogger(__name__)


class CatalogManager:
    """Manage table registration + manifest updates for a single lake table."""

    def __init__(
        self,
        url: str,
        auth_token: str | None,
        namespace: str,
        table_name: str,
        bucket: str,
        workspace_id: str,
        s3_prefix: str,
        expected_partitions: list[str],
    ) -> None:
        self._client = QuixTSDataLakeCatalogClient(url, auth_token)
        self.namespace = namespace
        self.table_name = table_name
        self.bucket = bucket
        self.workspace_id = workspace_id
        self.s3_prefix = s3_prefix
        self.expected_partitions = list(expected_partitions)
        self.registered = False
        self._lock = threading.Lock()

    @property
    def _table_path(self) -> str:
        return f"/namespaces/{self.namespace}/tables/{self.table_name}"

    def _table_location(self) -> str:
        """Full S3 URI of the table root (DuckDB reads this via the catalog)."""
        if self.workspace_id:
            return (
                f"s3://{self.bucket}/{self.workspace_id}/"
                f"{self.s3_prefix}/{self.table_name}"
            )
        return f"s3://{self.bucket}/{self.s3_prefix}/{self.table_name}"

    def _file_uri(self, storage_key: str) -> str:
        """Full S3 URI of one data file from its workspace-relative key."""
        if self.workspace_id:
            return f"s3://{self.bucket}/{self.workspace_id}/{storage_key}"
        return f"s3://{self.bucket}/{storage_key}"

    def ensure_table_registered(self) -> None:
        """Register the table if it is not already present in the catalog.

        Idempotent and thread-safe: GET the table first (200 => already there),
        otherwise PUT-create it with an empty partition spec so partitions are
        discovered from the Hive paths on first ``add-files``.
        """
        with self._lock:
            if self.registered:
                return

            check = self._client.get(self._table_path, timeout=10)
            if check.status_code == 200:
                logger.info(
                    "Table '%s' already registered in catalog", self.table_name
                )
                self.registered = True
                return

            logger.info(
                "Table '%s' not found (status %s); creating in catalog",
                self.table_name,
                check.status_code,
            )
            create = self._client.put(
                self._table_path,
                json={
                    "location": self._table_location(),
                    "partition_spec": [],
                    "properties": {
                        "created_by": "lake-zip-loader",
                        "auto_discovered": "false",
                        "expected_partitions": self.expected_partitions,
                    },
                },
                timeout=30,
            )
            if create.status_code in (200, 201):
                logger.info(
                    "Registered table '%s' in catalog (location=%s)",
                    self.table_name,
                    self._table_location(),
                )
                self.registered = True
            else:
                raise RuntimeError(
                    f"Failed to create table '{self.table_name}' in catalog: "
                    f"{create.status_code} {create.text}"
                )

    def register_file(
        self,
        storage_key: str,
        file_size: int,
        partition_values: dict[str, str],
        row_count: int,
    ) -> None:
        """Add one written parquet file to the table manifest."""
        if not self.registered:
            # Self-heal a transient startup registration failure.
            self.ensure_table_registered()

        entry = {
            "file_path": self._file_uri(storage_key),
            "file_size": file_size,
            "last_modified": datetime.now(tz=timezone.utc).isoformat(),
            "partition_values": {k: str(v) for k, v in partition_values.items()},
            "row_count": row_count,
        }
        resp = self._client.post(
            f"{self._table_path}/manifest/add-files",
            json={"files": [entry]},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"manifest add-files failed: {resp.status_code} {resp.text}"
            )
