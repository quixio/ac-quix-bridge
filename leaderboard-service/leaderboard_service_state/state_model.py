"""Pure, dependency-free helpers for the nested leaderboard State payload.

The durable store is QuixStreams' native State (RocksDB). Per ``experiment`` key
the stored value is a plain JSON-serialisable nested dict::

    {
      "_env": "<environment>",            # constant per experiment
      "<track>": {
        "<carModel>": {
          "<folded_driver>": {
            "best_lap_ms": 91234,
            "best_lap_number": 7,
            "gate_vector": [/* GATE_COUNT ints, cumulative ms */]
          }
        }
      }
    }

This is the cache's nested shape (``track -> carModel -> driver``) carrying a
richer per-driver *record* instead of a scalar ms. ``_env`` is a sibling marker
(leading underscore can never collide with a real track name) because environment
is a single constant for a given experiment in this single-sim deployment.

Driver keys are **folded** (NFKD + ASCII lowercase) by the caller so the value
matches ``_gate_vectors_cache``'s folded keying and ``gate_math`` consumes it
unchanged.

Everything here is a pure function over that dict: no Kafka, no RocksDB, no I/O —
unit-testable with a plain dict.
"""

from __future__ import annotations

from typing import Any

# AC reports iBestTime / a stub lap as INT_MAX (2**31 - 1) when no valid lap
# exists. Treat that value (and anything at or above it) as "no lap". Filtered
# on every write path into State.
INT_MAX = 2147483647

# Sibling key carrying the (constant-per-experiment) environment string.
ENV_KEY = "_env"

# Per-driver record keys.
BEST_LAP_MS = "best_lap_ms"
BEST_LAP_NUMBER = "best_lap_number"
GATE_VECTOR = "gate_vector"


def fold_best_lap(
    payload: dict[str, Any] | None,
    track: str,
    car_model: str,
    driver: str,
    lap_ms: int,
    gate_vector: list[int],
    lap_number: int,
    *,
    environment: str = "",
) -> tuple[dict[str, Any], bool]:
    """Min-update ``payload[track][carModel][driver]`` with a best-lap record.

    Replaces the whole ``{best_lap_ms, best_lap_number, gate_vector}`` record
    only on a strictly-faster lap (a brand-new entry or a new best). A non-positive
    or INT_MAX-stub *lap_ms*, a blank *track* / *carModel* / *driver*, or an empty
    *gate_vector* is a no-op that returns ``changed=False``. *payload* may be
    ``None`` (cold key); a fresh dict is created and returned.

    Returns ``(payload, changed)``. Callers persist only when ``changed`` is
    ``True``.
    """
    result: dict[str, Any] = dict(payload) if payload else {}

    if lap_ms <= 0 or lap_ms >= INT_MAX:
        return result, False
    if not (track and car_model and driver):
        return result, False
    if not gate_vector:
        return result, False

    if environment and result.get(ENV_KEY) != environment:
        result[ENV_KEY] = environment

    track_map = dict(result.get(track) or {})
    car_map = dict(track_map.get(car_model) or {})
    prev = car_map.get(driver)

    if isinstance(prev, dict):
        prev_ms = int(prev.get(BEST_LAP_MS) or 0)
        if 0 < prev_ms <= lap_ms:
            return result, False

    car_map[driver] = {
        BEST_LAP_MS: int(lap_ms),
        BEST_LAP_NUMBER: int(lap_number),
        GATE_VECTOR: [int(v) for v in gate_vector],
    }
    track_map[car_model] = car_map
    result[track] = track_map
    return result, True


def iter_records(
    experiment: str, payload: dict[str, Any] | None
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Flatten *payload* to ``[(track, carModel, folded_driver, record), ...]``.

    Skips the ``_env`` sibling marker and any malformed level. Each *record* is
    the ``{best_lap_ms, best_lap_number, gate_vector}`` dict. INT_MAX / non-positive
    best laps are dropped defensively. Pure — used by ``to_historicals`` and
    ``to_standings_rows``.
    """
    if not payload:
        return []
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for track, car_map in payload.items():
        if track == ENV_KEY or not isinstance(car_map, dict):
            continue
        for car_model, driver_map in car_map.items():
            if not isinstance(driver_map, dict):
                continue
            for driver, record in driver_map.items():
                if not isinstance(record, dict):
                    continue
                try:
                    ms = int(record.get(BEST_LAP_MS) or 0)
                except (TypeError, ValueError):
                    continue
                if ms <= 0 or ms >= INT_MAX:
                    continue
                out.append((str(track), str(car_model), str(driver), record))
    return out


def to_historicals(
    experiment: str, payload: dict[str, Any] | None
) -> dict[tuple[str, str, str], dict[str, dict[str, Any]]]:
    """Flatten *payload* to ``{(track, car, experiment): {folded_driver: record}}``.

    The value records (``{best_lap_ms, best_lap_number, gate_vector}``) are exactly
    the fields ``_HistoricalEntry`` carries, so the read-path layer can adapt them
    to ``_HistoricalEntry`` instances with no transformation. Pure — no IO, no
    ``_HistoricalEntry`` import (avoids the ``live_telemetry`` cycle).
    """
    out: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for track, car_model, driver, record in iter_records(experiment, payload):
        group_key = (track, car_model, experiment)
        out.setdefault(group_key, {})[driver] = {
            BEST_LAP_MS: int(record.get(BEST_LAP_MS) or 0),
            BEST_LAP_NUMBER: int(record.get(BEST_LAP_NUMBER) or 0),
            GATE_VECTOR: [int(v) for v in (record.get(GATE_VECTOR) or [])],
        }
    return out


def to_standings_rows(
    experiment: str, payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Flatten *payload* to historical (non-active) leaderboard rows.

    Each row is ``{environment, experiment, track, carModel, driver, best_lap_ms,
    best_lap_number}`` with ``driver`` the folded key. Sorted by
    ``(track, carModel, best_lap_ms)`` — fastest first within group. Used to seed
    the per-group historicals the active-row assembly merges against.
    """
    environment = environment_of(payload)
    rows: list[dict[str, Any]] = []
    for track, car_model, driver, record in iter_records(experiment, payload):
        rows.append(
            {
                "environment": environment,
                "experiment": experiment,
                "track": track,
                "carModel": car_model,
                "driver": driver,
                "best_lap_ms": int(record.get(BEST_LAP_MS) or 0),
                "best_lap_number": int(record.get(BEST_LAP_NUMBER) or 0),
            }
        )
    rows.sort(key=lambda r: (r["track"], r["carModel"], r["best_lap_ms"]))
    return rows


def environment_of(payload: dict[str, Any] | None) -> str:
    """Return the ``_env`` sibling marker (empty string when absent)."""
    if not payload:
        return ""
    return str(payload.get(ENV_KEY) or "")


def count_stats(payload: dict[str, Any] | None) -> tuple[int, int, int]:
    """Return ``(tracks, car_groups, driver_entries)`` — O(n) counts only.

    For the cheap per-GET ``state.get`` stat log (no payload dump, no
    serialization). ``tracks`` counts track keys (excluding ``_env``);
    ``car_groups`` counts ``(track, car)`` pairs; ``driver_entries`` counts leaf
    driver records.
    """
    if not payload:
        return 0, 0, 0
    tracks = 0
    car_groups = 0
    driver_entries = 0
    for track, car_map in payload.items():
        if track == ENV_KEY or not isinstance(car_map, dict):
            continue
        tracks += 1
        for _car_model, driver_map in car_map.items():
            if not isinstance(driver_map, dict):
                continue
            car_groups += 1
            driver_entries += sum(
                1 for v in driver_map.values() if isinstance(v, dict)
            )
    return tracks, car_groups, driver_entries
