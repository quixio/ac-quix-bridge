"""Seed local Mongo with real completed post-race analyses + their tests.

For local viewing of the telemetry-viz feature. Loads the JSON captured from the
quixdev Test Manager API (scripts/seed_data/) into the local `test_manager` DB:

- every Test (with its `sessions[]` track/car) → `tests` collection
- every completed Analysis → `analyses` collection, with `context`
  (driver/track/car_model) stamped from the matching test+session when the
  source doc has none (these analyses predate the context feature; stamping
  mirrors what the create-path now does so `resolve_lake_keys` can resolve).

Idempotent (upsert by _id). Run inside the backend container:
    docker compose -f docker-compose.dev.yml exec backend \\
        uv run python scripts/seed_local_viz.py
"""

import glob
import json
import os

from pymongo import MongoClient

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_DIR = os.path.join(HERE, "seed_data")


def _connect() -> "MongoClient[dict]":
    user = os.environ["MONGO_USER"]
    pw = os.environ["MONGO_PASSWORD"]
    host = os.environ.get("MONGO_HOST", "mongodb")
    port = os.environ.get("MONGO_PORT", "27017")
    return MongoClient(f"mongodb://{user}:{pw}@{host}:{port}/?authSource=admin")


def main() -> None:
    db_name = os.environ.get("MONGO_DATABASE", "test_manager")
    mongo = _connect()[db_name]

    # Tests: the API serializes the PK as `test_id` (aliased `_id` in Mongo).
    # Re-key to `_id` so `mongo.tests.find_one({"_id": ...})` + `Test(**doc)` work.
    tests: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(SEED_DIR, "test_*.json"))):
        with open(path, encoding="utf-8") as fh:
            t = json.load(fh)
        tid = t.pop("test_id")
        t["_id"] = tid
        tests[tid] = t

    def context_for(test_id: str | None, session_id: str | None) -> dict | None:
        t = tests.get(test_id or "")
        if not t:
            return None
        driver = t.get("driver")
        track = car = None
        for s in t.get("sessions", []):
            if s.get("session_id") == session_id:
                track, car = s.get("track"), s.get("car_model")
                break
        if not (driver or track or car):
            return None
        return {"driver": driver, "track": track, "car_model": car}

    for tid, t in tests.items():
        mongo.tests.replace_one({"_id": tid}, t, upsert=True)
    print(f"seeded {len(tests)} tests: {', '.join(sorted(tests))}")

    with open(os.path.join(SEED_DIR, "analyses_complete.json"), encoding="utf-8") as fh:
        analyses = json.load(fh)["items"]

    seeded = 0
    for a in analyses:
        # Analyses serialize their PK as `id` (aliased `_id`); `test_id` is the FK
        # and must stay.
        if "id" in a and "_id" not in a:
            a["_id"] = a.pop("id")
        if not a.get("context"):
            ctx = context_for(a.get("test_id"), a.get("session_id"))
            if ctx:
                a["context"] = ctx
        mongo.analyses.replace_one({"_id": a["_id"]}, a, upsert=True)
        seeded += 1
        ctx = a.get("context") or {}
        print(
            f"  analysis {a['_id'][:8]} test={a.get('test_id')} "
            f"session={a.get('session_id')} "
            f"ctx={ctx.get('driver')}/{ctx.get('track')}/{ctx.get('car_model')}"
        )
    print(f"seeded {seeded} analyses")


if __name__ == "__main__":
    main()
