from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MongoSettings(BaseSettings):
    """Mongo connection settings.

    Copied verbatim (same env_prefix, fields, defaults and computed `url`)
    from test-manager-backend/api/settings.py so this one-shot job shares the
    deployment's injected MONGO_* secrets. Kept as a local copy rather than a
    cross-import because the two apps have separate Docker build contexts.

    DB-name note: the default database is `test_manager` (the DB the backend
    actually connects to). The quix.yaml DCM config references `ac_telemetry`
    in places; if the bridge ends up serving track geometry from a different
    DB, override via the MONGO_DATABASE env var (or --database) with no code
    change.
    """

    model_config = SettingsConfigDict(env_prefix="MONGO_")

    user: str = Field(..., description="MongoDB username")
    password: str = Field(..., description="MongoDB password")
    host: str = Field("mongodb", description="MongoDB host address")
    port: int = Field(27017, description="MongoDB port")
    database: str = Field("test_manager", description="MongoDB database name")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return f"mongodb://{self.user}:{self.password}@{self.host}:{self.port}"
