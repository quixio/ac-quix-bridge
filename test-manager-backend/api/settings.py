from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Nested settings
    mongo: MongoSettings = Field(default_factory=MongoSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # pydantic-settings loads required Quix__Workspace__Id / Quix__Sdk__Token
    # from environment via aliases; ty can't see that.
    return Settings()  # ty: ignore[missing-argument]
