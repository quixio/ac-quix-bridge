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

    # Direct QuixLake connection — used by the leaderboard endpoint to query
    # the shared lake without going through the Settings-UI deployment ref.
    # Matches the env vars wired on the Telemetry Explorer deployment in
    # `quix.yaml`; both deployments talk to the same lake.
    quixlake_url: str | None = Field(
        None,
        alias="QUIXLAKE_URL",
        description="Base URL of the shared QuixLake instance",
    )
    quix_lake_token: str | None = Field(
        None,
        alias="QUIX_LAKE_TOKEN",
        description="PAT authenticating against the shared QuixLake",
    )

    # Lake table the leaderboard SQL builders read from. Defaults to
    # `ac_telemetry` (current behaviour); set `LAKE_TABLE` to e.g.
    # `ac_telemetry_leaderboard` to point a deployment at an alternate
    # table without code changes. Value must match `[A-Za-z_][A-Za-z0-9_]*`
    # because it is inlined into SQL statements.
    lake_table: str = Field(
        "ac_telemetry",
        alias="LAKE_TABLE",
        description="Lake table name used by leaderboard SQL builders",
    )

    @field_validator("lake_table")
    @classmethod
    def _validate_lake_table(cls, value: str) -> str:
        if not _LAKE_TABLE_PATTERN.match(value):
            raise ValueError(
                f"LAKE_TABLE must match {_LAKE_TABLE_PATTERN.pattern!r}; got {value!r}"
            )
        return value

    # Nested settings
    mongo: MongoSettings = Field(default_factory=MongoSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # pydantic-settings loads required Quix__Workspace__Id / Quix__Sdk__Token
    # from environment via aliases; ty can't see that.
    return Settings()  # ty: ignore[missing-argument]
