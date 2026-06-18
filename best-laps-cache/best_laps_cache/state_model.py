"""Pure, dependency-free helpers for the nested best-laps State payload.

The durable store is QuixStreams' native State (RocksDB). Per experiment key
the stored value is a plain JSON-serialisable nested dict::

    {
      "_env": "<environment>",          # environment is constant per experiment
      "<track>": {
        "<carModel>": {
          "<driver>": best_lap_ms_int
        }
      }
    }

``_env`` is a sibling marker (prefixed with ``_`` so it can never collide with a
real track name) rather than an extra nesting level, because environment is a
single constant for a given experiment in this single-sim deployment.

Everything in this module is a pure function over that dict: no Kafka, no
RocksDB, no I/O. The stateful SDF callbacks call :func:`fold_lap` to mutate the
payload and :func:`to_rows` to flatten it for the leaderboard table / HTTP
response. That keeps the State semantics unit-testable with no broker.
"""

from __future__ import annotations

from typing import Any

# AC reports iBestTime as INT_MAX (2**31 - 1) when no valid lap has been set.
# Treat that stub value — and anything at or above it — as "no lap", never a
# real best. Filtered on every write path into State.
INT_MAX = 2147483647

# Sibling key carrying the (constant-per-experiment) environment string. Leading
# underscore guarantees no clash with a real track name.
ENV_KEY = "_env"


def fold_lap(
    payload: dict[str, Any] | None,
    track: str,
    car_model: str,
    driver: str,
    best_ms: int,
    *,
    environment: str = "",
) -> tuple[dict[str, Any], bool]:
    """Min-update ``payload[track][carModel][driver]`` with *best_ms*.

    Returns ``(payload, changed)`` where *changed* is ``True`` only on a
    strictly-faster write (a brand-new entry or a new best). A non-positive or
    INT_MAX-stub *best_ms*, or a blank *track* / *carModel* / *driver*, is a
    no-op that returns ``changed=False``. The input *payload* may be ``None``
    (cold key); a fresh dict is created and returned.

    The returned payload is always the dict to ``state.set(experiment, ...)``;
    callers should only persist when ``changed`` is ``True``.
    """
    result: dict[str, Any] = dict(payload) if payload else {}

    if best_ms <= 0 or best_ms >= INT_MAX:
        return result, False
    if not (track and car_model and driver):
        return result, False

    if environment and result.get(ENV_KEY) != environment:
        # Record/refresh the environment marker; not a "best lap changed" event
        # on its own, so it does not flip ``changed`` unless a lap also lands.
        result[ENV_KEY] = environment

    track_map = dict(result.get(track) or {})
    car_map = dict(track_map.get(car_model) or {})
    prev = car_map.get(driver)

    if prev is not None and best_ms >= int(prev):
        return result, False

    car_map[driver] = int(best_ms)
    track_map[car_model] = car_map
    result[track] = track_map
    return result, True


def to_rows(experiment: str, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten a nested experiment payload into sorted leaderboard rows.

    Each row is ``{environment, experiment, track, carModel, driver,
    best_lap_ms}``. Rows are sorted by ``(track, carModel, best_lap_ms)`` —
    fastest first within each track/car group. The ``_env`` sibling marker is
    skipped (it is not a track). INT_MAX-stub values are defensively dropped.
    """
    if not payload:
        return []

    environment = str(payload.get(ENV_KEY) or "")
    rows: list[dict[str, Any]] = []
    for track, car_map in payload.items():
        if track == ENV_KEY or not isinstance(car_map, dict):
            continue
        for car_model, driver_map in car_map.items():
            if not isinstance(driver_map, dict):
                continue
            for driver, best_ms in driver_map.items():
                try:
                    ms = int(best_ms)
                except (TypeError, ValueError):
                    continue
                if ms <= 0 or ms >= INT_MAX:
                    continue
                rows.append(
                    {
                        "environment": environment,
                        "experiment": experiment,
                        "track": str(track),
                        "carModel": str(car_model),
                        "driver": str(driver),
                        "best_lap_ms": ms,
                    }
                )

    rows.sort(key=lambda r: (r["track"], r["carModel"], r["best_lap_ms"]))
    return rows


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    track: str | None = None,
    car_model: str | None = None,
) -> list[dict[str, Any]]:
    """Filter flattened rows by *track* and/or *car_model* (exact match).

    Experiment is intrinsic to the State key, so it is never a filter here
    (per ``api-wrapper-requirement.md``). A ``None`` dimension matches
    everything. This is the core the GET wrapper calls over the materialized
    current view.
    """
    return [
        row
        for row in rows
        if (track is None or row["track"] == track)
        and (car_model is None or row["carModel"] == car_model)
    ]
