import os
import sys

import uvicorn

from api.app import create_app
from api.settings import get_settings


def main() -> int:
    settings = get_settings()

    # Enable hot reload in local development mode
    is_local_dev = os.getenv("LOCAL_DEV_MODE", "false").lower() == "true"

    if is_local_dev:
        # In local dev: use import string for hot reload (workers must be 1)
        uvicorn.run(
            "api.app:app",  # Import string for reload to work
            host=settings.api_host,
            port=settings.api_port,
            reload=True,  # Auto-reload when code changes
            reload_dirs=["/app"],  # Watch /app directory
        )
    else:
        # In production: use app object with multiple workers
        uvicorn.run(
            app=create_app(),
            host=settings.api_host,
            port=settings.api_port,
            workers=settings.api_workers,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
