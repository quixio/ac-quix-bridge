"""Mongo connection settings for the telemetry explorer.

Mirrors track-viewer/app/settings.py (same MONGO_* env names, defaults and
computed `url`) so this read-only consumer shares the deployment's injected
MONGO_* secrets. Implemented with plain `os.getenv` rather than
pydantic-settings BaseSettings so the only NEW runtime dependency this feature
adds is `pymongo` (pydantic-settings is not a direct dep of this app).

DB-name note: the default database is `test_manager` — the DB the
track-importer Job writes `track_layouts` into and that test-manager-backend
connects to. The quix.yaml DCM config references `ac_telemetry` in places; if
the explorer needs to read geometry from a different DB, override via the
MONGO_DATABASE env var with no code change (keep `test_manager` by default).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Collection holding the importer's layout documents.
TRACK_LAYOUTS_COLLECTION = os.getenv("TRACK_LAYOUTS_COLLECTION", "track_layouts")
# Cap on geometry points returned per /api/track response; larger layouts are
# uniformly stride-sampled down to this many (first + last + corner boundaries
# preserved). Bounds payload size on Nordschleife-class layouts.
TRACK_MAX_POINTS = int(os.getenv("TRACK_MAX_POINTS", "3000"))


@dataclass(frozen=True)
class MongoSettings:
    """MongoDB connection parameters, read from MONGO_* env vars.

    Field names, defaults and the computed `url` match
    track-viewer/app/settings.py:MongoSettings.
    """

    user: str
    password: str
    host: str = "mongodb"
    port: int = 27017
    database: str = "test_manager"

    @property
    def url(self) -> str:
        return f"mongodb://{self.user}:{self.password}@{self.host}:{self.port}"

    @classmethod
    def from_env(cls) -> MongoSettings:
        return cls(
            user=os.getenv("MONGO_USER", ""),
            password=os.getenv("MONGO_PASSWORD", ""),
            host=os.getenv("MONGO_HOST", "mongodb"),
            port=int(os.getenv("MONGO_PORT", "27017")),
            database=os.getenv("MONGO_DATABASE", "test_manager"),
        )
