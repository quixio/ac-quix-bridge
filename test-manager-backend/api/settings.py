import re
from functools import lru_cache

from pydantic import Field, computed_field, field_validator
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

    # TODO: Remove this to enforce authentication
    api_auth_active: bool = Field(
        True, description="Whether API authentication is active"
    )

    # Quix settings
    workspace_id: str = Field(
        alias="Quix__Workspace__Id", description="Quix workspace ID"
    )
    sdk_token: str = Field(alias="Quix__Sdk__Token", description="SDK token")

    # Configuration API settings
    config_api_url: str = Field(..., description="Configuration API URL")

    # Integration services URLs
    measurements_workspace_id: str | None = Field(
        None,
        description="Workspace ID for measurements services (defaults to current workspace)",
    )
    measurements_topic_name: str | None = Field(
        None, description="Topic/table name for test measurements in the Data Lake"
    )

    # Quix Lakehouse Query API — used by the leaderboard endpoints to run
    # SQL against the shared lake. Auto-injected by Box Cloud as
    # Quix__Lakehouse__Query__Url and Quix__Lakehouse__Query__AuthToken.
    # Both deployments (test-manager-backend, telemetry-comparison) talk
    # to the same Lakehouse instance.
    lakehouse_query_url: str | None = Field(
        None,
        alias="Quix__Lakehouse__Query__Url",
        description="Base URL of the Quix Lakehouse Query API",
    )
    lakehouse_query_token: str | None = Field(
        None,
        alias="Quix__Lakehouse__Query__AuthToken",
        description="Bearer token for the Lakehouse Query API",
    )

    # Kafka topic names for the live leaderboard consumer. QuixStreams
    # Application() broker params are auto-resolved from Quix__Broker__*
    # env vars — no explicit broker settings needed here.
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

    # Lake table the leaderboard SQL builders read from. Defaults to
    # `ac_telemetry_leadboard` (intentional typo — matches the Box Cloud
    # Lakehouse table name from dev-planning/leaderboard-consolidated/spec.md
    # §3.11). Set `LAKE_TABLE` to override without code changes.
    # Value must match `[A-Za-z_][A-Za-z0-9_]*` because it is inlined
    # into SQL statements.
    lake_table: str = Field(
        "ac_telemetry_leadboard",
        alias="LAKE_TABLE",
        description="Lake table name used by leaderboard SQL builders",
    )

    # Column-name overrides — some derived tables (e.g. ac_telemetry_leadboard)
    # drop the `i` prefix and use `currentTime` / `bestTime` /
    # `normalizedPosition` instead of AC's raw `iCurrentTime` / `iBestTime` /
    # `normalizedCarPosition`. Same identifier-validation rule as `lake_table`
    # since these are inlined into SQL too.
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
    # pydantic-settings loads required Quix__Workspace__Id / Quix__Sdk__Token
    # from environment via aliases; ty can't see that.
    return Settings()  # ty: ignore[missing-argument]
