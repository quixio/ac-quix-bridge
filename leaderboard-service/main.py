import logging
import logging.config
import os
import sys
import time

import uvicorn
from asgi_correlation_id import CorrelationIdFilter

from api.app import create_app
from api.settings import get_settings


_LOG_FORMAT = (
    "%(asctime)s %(levelname)-7s %(name)s [req=%(correlation_id)s] %(message)s"
)


class _UtcIsoFormatter(logging.Formatter):
    """Render asctime as ISO 8601 UTC with millisecond precision and a Z suffix."""

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    @staticmethod
    def converter(timestamp: float | None) -> time.struct_time:
        return time.gmtime(timestamp)


def _build_log_config() -> dict:
    """Return a dictConfig that pipes every logger (incl. uvicorn) through one handler."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "correlation_id": {
                "()": CorrelationIdFilter,
                "default_value": "-",
            },
        },
        "formatters": {
            "default": {
                "()": _UtcIsoFormatter,
                "format": _LOG_FORMAT,
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "default",
                "filters": ["correlation_id"],
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

    settings = get_settings()

    is_local_dev = os.getenv("LOCAL_DEV_MODE", "false").lower() == "true"

    if is_local_dev:
        uvicorn.run(
            "api.app:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=True,
            reload_dirs=["/app/api"],
            reload_excludes=["*.pyc", "**/__pycache__/**"],
            log_config=log_config,
        )
    else:
        uvicorn.run(
            app=create_app(),
            host=settings.api_host,
            port=settings.api_port,
            workers=settings.api_workers,
            log_config=log_config,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
