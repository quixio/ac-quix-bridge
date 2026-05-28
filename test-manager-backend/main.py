import logging
import os
import sys

import uvicorn

from api.app import create_app
from api.settings import get_settings



def main() -> int:
    # Configure root logger so application loggers (api.auth, api.routes.*, etc.)
    # actually emit. Without this, Python's default WARNING level swallows INFO.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s:%(name)s:%(message)s",
    )

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
            # Only watch api/ (source). Excluding scripts/ and bytecode avoids
            # spurious restarts when ad-hoc scripts compile .pyc files.
            reload_dirs=["/app/api"],
            reload_excludes=["*.pyc", "**/__pycache__/**"],
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
