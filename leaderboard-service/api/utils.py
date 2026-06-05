from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def timestamp() -> int:
    return int(now().timestamp())
