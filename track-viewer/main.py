import logging
import logging.config
import os
import sys

import uvicorn

from app.api import create_app
from app.settings import ViewerSettings

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def _build_log_config() -> dict:
    """Pipe every logger (incl. uvicorn) through one stdout handler."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": _LOG_FORMAT}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "default",
            },
        },
        "loggers": {
            "": {
                "level": os.getenv("LOG_LEVEL", "INFO").upper(),
                "handlers": ["default"],
            },
            "uvicorn": {"level": "INFO", "handlers": ["default"], "propagate": False},
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["default"],
                "propagate": False,
            },
            "uvicorn.error": {
                "level": "INFO",
                "handlers": ["default"],
                "propagate": False,
            },
        },
    }


def main() -> int:
    log_config = _build_log_config()
    logging.config.dictConfig(log_config)

    settings = ViewerSettings()
    uvicorn.run(
        app=create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_config=log_config,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
