from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MongoSettings(BaseSettings):
    """Mongo connection settings.

    Copied verbatim (same env_prefix, fields, defaults and computed `url`)
    from test-manager-backend/api/settings.py and track-importer/settings.py
    so this read-only viewer shares the deployment's injected MONGO_* secrets.
    Kept as a local copy rather than a cross-import because the apps have
    separate Docker build contexts.

    DB-name note: the default database is `test_manager` (the DB the backend
    connects to and the DB the track-importer Job writes track_layouts into).
    The quix.yaml DCM config references `ac_telemetry` in places; if the
    viewer needs to read geometry from a different DB, override via the
    MONGO_DATABASE env var with no code change.
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


class ViewerSettings(BaseSettings):
    """Viewer-local settings (HTTP bind + collection name)."""

    api_host: str = Field("0.0.0.0", description="Host address")
    api_port: int = Field(8080, description="Port number")
    collection: str = Field(
        "track_layouts", description="Mongo collection holding layout documents"
    )
    # Cap on geometry points returned to the client; larger layouts are
    # uniformly stride-sampled down to this many points (first + last kept).
    max_points: int = Field(3000, description="Max geometry points per response")
