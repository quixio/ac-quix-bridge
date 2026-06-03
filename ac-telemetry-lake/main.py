"""
Quix TS Datalake Sink - Main Entry Point

This application consumes data from a Kafka topic and writes it to blob storage as
Hive-partitioned Parquet files with optional Iceberg catalog registration.

Blob storage is configured via the Quix__BlobStorage__Connection__Json environment variable,
which is automatically handled by the quixportal library. The bucket name is extracted
automatically from this configuration.

File paths follow the workspace-aware structure:
    {workspaceId}/data-lake/time-series/{table_name}/...
"""
import os
import logging

from quixstreams import Application
from quixstreams.dataframe.joins.lookups import QuixConfigurationService
from quixstreams.sinks.core.quix_ts_datalake_sink import QuixTSDataLakeSink

# Configure logging
logging.basicConfig(
    level=os.getenv("LOGLEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constant for time-series data lake path structure
TIMESERIES_PREFIX = "data-lake/time-series"


def _positive_int(env_var: str, default: str) -> int:
    raw = os.getenv(env_var, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{env_var} must be a positive integer, got '{raw}'")
    if value <= 0:
        raise ValueError(f"{env_var} must be a positive integer, got {value}")
    return value

def parse_hive_columns(columns_str: str) -> list:
    """
    Parse comma-separated list of partition columns.

    Args:
        columns_str: Comma-separated column names (e.g., "year,month,day")

    Returns:
        List of column names, or empty list if input is empty
    """
    if not columns_str or columns_str.strip() == "":
        return []
    return [col.strip() for col in columns_str.split(",") if col.strip()]


# Initialize Quix Streams Application
app = Application(
    consumer_group=os.getenv("CONSUMER_GROUP", "s3_direct_sink_v1.0"),
    auto_offset_reset=os.getenv("AUTO_OFFSET_RESET", "latest"),
    commit_interval=int(os.getenv("COMMIT_INTERVAL", "30")),
    commit_every=int(os.getenv("BATCH_SIZE", "10000"))
)

# Parse configuration
hive_columns = parse_hive_columns(os.getenv("HIVE_COLUMNS", ""))
auto_discover = os.getenv("AUTO_DISCOVER", "true").lower() == "true"
table_name = os.getenv("TABLE_NAME") or os.environ["input"]

# Workspace ID (automatically injected by Quix platform)
workspace_id = os.getenv("Quix__Workspace__Id", "")

# Initialize QuixLakeSink
# Note: Blob storage credentials are configured via Quix__BlobStorage__Connection__Json
# environment variable, which is automatically read by quixportal.
# The bucket name is extracted automatically from the quixportal configuration.
# Quix Portal injects the Catalog URL under both the Quix naming convention
# (`Quix__Lakehouse__Catalog__Url`) and the PyIceberg one (`CATALOG_URL`) when a Lakehouse Catalog
# deployment exists in the workspace; prefer the Quix name, fall back to the PyIceberg one for
# legacy compatibility. The auth token is only injected under the Quix name — it routes via the
# secrets-bag / secretKeyRef path that the platform uses for the Catalog's own credentials.
blob_sink = QuixTSDataLakeSink(
    s3_prefix=TIMESERIES_PREFIX,
    table_name=table_name,
    workspace_id=workspace_id,
    hive_columns=hive_columns,
    timestamp_column=os.getenv("TIMESTAMP_COLUMN", "ts_ms"),
    catalog_url=os.getenv("Quix__Lakehouse__Catalog__Url") or os.getenv("CATALOG_URL"),
    catalog_auth_token=os.getenv("Quix__Lakehouse__Catalog__AuthToken"),
    auto_discover=auto_discover,
    namespace=os.getenv("CATALOG_NAMESPACE", "default"),
    auto_create_bucket=True,
    max_workers=_positive_int("MAX_WRITE_WORKERS", "10"),
    on_client_connect_success=lambda: print("CONNECTED!"),
    on_client_connect_failure=lambda e: print(f"ERROR! {e}"),
)

# Create streaming dataframe
sdf = app.dataframe(topic=app.topic(os.environ["input"], key_deserializer="str"))

# --- Enrich with Dynamic Configuration Manager ---
config_topic_name = os.getenv("config_topic", "ac-telemetry-config")
config_topic = app.topic(config_topic_name)

config_lookup = QuixConfigurationService(
    topic=config_topic,
    app_config=app.config,
)

# Enrich with experiment config (from form)
sdf = sdf.join_lookup(
    lookup=config_lookup,
    fields={
        "test_id": config_lookup.json_field(jsonpath="$.test_id", type="experiment", default="NA"),
        "environment": config_lookup.json_field(jsonpath="$.environment", type="experiment", default="NA"),
        "test_rig": config_lookup.json_field(jsonpath="$.test_rig", type="experiment", default="NA"),
        "experiment": config_lookup.json_field(jsonpath="$.experiment_id", type="experiment", default="NA"),
        "driver": config_lookup.json_field(jsonpath="$.driver", type="experiment", default="NA"),
        "carModel": config_lookup.json_field(jsonpath="$.carModel", type="session", default="NA"),
        "track": config_lookup.json_field(jsonpath="$.track", type="session", default="NA"),
    },
)

# Add lap number (completedLaps=0 means lap 1 in progress)
sdf = sdf.fill(completedLaps=-1)
sdf["lap"] = sdf["completedLaps"] + 1

# Attach sink (batching is handled by BatchingSink)
sdf.sink(blob_sink)

# Log startup configuration
storage_path = f"{workspace_id}/{TIMESERIES_PREFIX}" if workspace_id else TIMESERIES_PREFIX
logger.info("Starting Quix TS Datalake Sink")
logger.info(f"  Input topic: {os.environ['input']}")
logger.info(f"  Storage path: {storage_path}/{table_name}")
logger.info(f"  Partitioning: {hive_columns if hive_columns else 'none'}")

if __name__ == "__main__":
    app.run()