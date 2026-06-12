import re
from functools import lru_cache

from pydantic import AliasChoices, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Lake table names are inlined into SQL (QuixLake doesn't expose
# parameterised queries), so anything other than a plain SQL identifier
# is an injection vector. Reject at settings load time rather than per
# query so a bad env var fails the deployment loudly.
_LAKE_TABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MongoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONGO_")

    user: str = Field(..., description="MongoDB username")
    password: str = Field(..., description="MongoDB password")
    host: str = Field("localhost", description="MongoDB host address")
    port: int = Field(27017, description="MongoDB port")
    database: str = Field("test_manager", description="MongoDB database name")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return f"mongodb://{self.user}:{self.password}@{self.host}:{self.port}"


class Settings(BaseSettings):
    # API settings
    api_host: str = Field("0.0.0.0", description="Host address")
    api_port: int = Field(8080, description="Port number")
    api_workers: int = Field(1, description="Number of workers")

    api_auth_active: bool = Field(
        False,
        description=(
            "Whether API authentication is active. Default off so the "
            "leaderboard demo works without portal-side variable wiring; "
            "set `API_AUTH_ACTIVE=true` in the deployment env to re-enable "
            "the validate_permissions / shared-secret gate in auth.py."
        ),
    )

    # Quix settings
    workspace_id: str = Field(
        alias="Quix__Workspace__Id", description="Quix workspace ID"
    )
    sdk_token: str = Field(alias="Quix__Sdk__Token", description="SDK token")

    # Configuration API settings
    config_api_url: str = Field(..., description="Configuration API URL")

    # Quix Lakehouse Query API. Accepts either the canonical
    # `Quix__Lakehouse__Query__*` env-var names OR the project-variable
    # names (`LAKE_API_URL` / `LAKE_API_TOKEN` / `QUIX_LAKE_TOKEN` /
    # `quix_lake_pat`) so the deployment works regardless of how the
    # Quix Cloud portal mapped the project variables to container env vars.
    lakehouse_query_url: str | None = Field(
        None,
        validation_alias=AliasChoices(
            "Quix__Lakehouse__Query__Url",
            "LAKE_API_URL",
            "QUIXLAKE_URL",
        ),
        description="Base URL of the Quix Lakehouse Query API",
    )
    lakehouse_query_token: str | None = Field(
        None,
        validation_alias=AliasChoices(
            "Quix__Lakehouse__Query__AuthToken",
            "LAKE_API_TOKEN",
            "QUIX_LAKE_TOKEN",
            "quix_lake_pat",
        ),
        description="Bearer token for the Lakehouse Query API",
    )

    # Kafka topic names for the live leaderboard consumer.
    kafka_raw_topic: str = Field(
        "demo-acquixbridge-dev-ac-telemetry-raw",
        alias="output",
        description="Kafka topic for raw AC telemetry (live leaderboard consumer)",
    )
    kafka_session_topic: str = Field(
        "demo-acquixbridge-dev-ac-telemetry-session",
        alias="session_output",
        description="Kafka topic for AC session events",
    )

    # Lake table the leaderboard SQL builders read from. Default matches
    # the deployed `LAKE_TABLE=ac_telemetry` (the previous
    # `ac_telemetry_leadboard` default was a typo'd footgun — it only ever
    # worked because the deployment overrode it).
    lake_table: str = Field(
        "ac_telemetry",
        alias="LAKE_TABLE",
        description="Lake table name used by leaderboard SQL builders",
    )

    col_current_time: str = Field(
        "iCurrentTime",
        alias="LAKE_COL_CURRENT_TIME",
        description="Column name for AC's lap-relative current time (ms)",
    )
    col_best_time: str = Field(
        "iBestTime",
        alias="LAKE_COL_BEST_TIME",
        description="Column name for AC's best-lap time so far (ms)",
    )
    col_normalized_position: str = Field(
        "normalizedCarPosition",
        alias="LAKE_COL_NORMALIZED_POSITION",
        description="Column name for the 0..1 lap position",
    )

    # Best-laps TTL cache + gate-vector rebuild knobs
    # (dev-planning/leaderboard-bestlaps-gates/spec.md §6.1).
    best_laps_ttl_seconds: float = Field(
        15.0,
        alias="BEST_LAPS_TTL_SECONDS",
        description=(
            "Age (seconds) after which a per-group best-laps cache entry "
            "is refreshed from the lake"
        ),
    )
    best_lap_match_tolerance_ms: int = Field(
        1500,
        alias="BEST_LAP_MATCH_TOLERANCE_MS",
        description=(
            "Max |lap_ms - iBestTime| (ms) for a per-lap scan row to be "
            "identified as the lap the best time was set on"
        ),
    )
    # Lake-first partition enumeration (api/partition_index.py): how long
    # one enumeration result is served before the lake is re-asked. Keep
    # this >= best_laps_ttl_seconds — the enumeration feeds `_known_groups`
    # which the best-laps TTL tick consults every poll iteration.
    partition_index_ttl_seconds: float = Field(
        60.0,
        alias="PARTITION_INDEX_TTL_SECONDS",
        description=(
            "Age (seconds) after which the lake partition-group enumeration "
            "is refreshed (also the failure backoff window)"
        ),
    )
    lake_server_aggregation: bool = Field(
        True,
        alias="LAKE_SERVER_AGGREGATION",
        description=(
            "Use server-side MIN(...) GROUP BY driver for best laps. Set "
            "false to force the raw-scan + Python-MIN fallback (needed if "
            "LAKE_TABLE points at a derived table where GROUP BY stalls)"
        ),
    )

    @field_validator(
        "lake_table",
        "col_current_time",
        "col_best_time",
        "col_normalized_position",
    )
    @classmethod
    def _validate_lake_identifier(cls, value: str) -> str:
        if not _LAKE_TABLE_PATTERN.match(value):
            raise ValueError(
                f"value must match {_LAKE_TABLE_PATTERN.pattern!r}; got {value!r}"
            )
        return value

    # Nested settings
    mongo: MongoSettings = Field(default_factory=MongoSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # ty: ignore[missing-argument]
