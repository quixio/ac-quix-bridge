"""Admin endpoints for database management and demo data."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pymongo.database import Database

from ..auth import update_permission
from ..mongo import get_mongo
from ..models import DeviceStatus, DeviceCategory, TestStatus

router = APIRouter()


@router.post("/admin/seed-demo-data", response_model=dict)
def seed_demo_data(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict:
    """Seed AC telemetry demo data: devices, drivers, environments, and tests."""
    now = datetime.now(timezone.utc)

    # Devices
    devices = [
        {"_id": "DEV-0001", "category": DeviceCategory.PC, "name": "XPS", "status": DeviceStatus.ACTIVE, "created_at": now - timedelta(days=30), "updated_at": now - timedelta(days=30)},
        {"_id": "DEV-0002", "category": DeviceCategory.PC, "name": "patrickpc", "status": DeviceStatus.ACTIVE, "created_at": now - timedelta(days=28), "updated_at": now - timedelta(days=28)},
        {"_id": "DEV-0003", "category": DeviceCategory.TEST_RIG, "name": "Logitech G29", "status": DeviceStatus.ACTIVE, "created_at": now - timedelta(days=25), "updated_at": now - timedelta(days=25)},
        {"_id": "DEV-0004", "category": DeviceCategory.TEST_RIG, "name": "Logitech G923", "status": DeviceStatus.ACTIVE, "created_at": now - timedelta(days=24), "updated_at": now - timedelta(days=24)},
        {"_id": "DEV-0005", "category": DeviceCategory.TEST_RIG, "name": "Fanatec DD Pro", "status": DeviceStatus.ACTIVE, "created_at": now - timedelta(days=20), "updated_at": now - timedelta(days=20)},
        {"_id": "DEV-0006", "category": DeviceCategory.TEST_RIG, "name": "Simucube", "status": DeviceStatus.INACTIVE, "created_at": now - timedelta(days=15), "updated_at": now - timedelta(days=5)},
    ]

    # Drivers
    drivers = [
        {"_id": "DRV-0001", "name": "Ludvik", "created_at": now - timedelta(days=30), "updated_at": now - timedelta(days=30)},
        {"_id": "DRV-0002", "name": "Daniel", "created_at": now - timedelta(days=30), "updated_at": now - timedelta(days=30)},
        {"_id": "DRV-0003", "name": "Patrick", "created_at": now - timedelta(days=30), "updated_at": now - timedelta(days=30)},
        {"_id": "DRV-0004", "name": "Peter", "created_at": now - timedelta(days=28), "updated_at": now - timedelta(days=28)},
        {"_id": "DRV-0005", "name": "Mike", "created_at": now - timedelta(days=28), "updated_at": now - timedelta(days=28)},
    ]

    # Environments
    environments = [
        {"_id": "ENV-0001", "name": "Prague Office", "location": "Prague, Czech Republic", "status": "active", "created_at": now - timedelta(days=30), "updated_at": now - timedelta(days=30)},
        {"_id": "ENV-0002", "name": "Patrick's Office", "location": "Remote", "status": "active", "created_at": now - timedelta(days=28), "updated_at": now - timedelta(days=28)},
    ]

    # Tests
    tests = [
        {
            "_id": "TEST-2025-001",
            "campaign_id": "tyre_pressure_comparison",
            "devices": [{"device_id": "DEV-0001", "device_version": None}, {"device_id": "DEV-0003", "device_version": None}],
            "environment_id": "ENV-0001",
            "environment_version": None,
            "operator": "Ludvik",
            "created_at": now - timedelta(days=10),
            "updated_at": now - timedelta(days=10),
            "sensors": {},
            "config_id": str(uuid4()),
            "config_type": "experiment",
            "target_key": "XPS",
            "config_version": 1,
            "links": [],
            "files": {},
            "status": TestStatus.DRAFT,
            "start": None,
            "end": None,
        },
        {
            "_id": "TEST-2025-002",
            "campaign_id": "baseline_lap_times",
            "devices": [{"device_id": "DEV-0002", "device_version": None}, {"device_id": "DEV-0005", "device_version": None}],
            "environment_id": "ENV-0002",
            "environment_version": None,
            "operator": "Patrick",
            "created_at": now - timedelta(days=7),
            "updated_at": now - timedelta(days=5),
            "sensors": {},
            "config_id": str(uuid4()),
            "config_type": "experiment",
            "target_key": "patrickpc",
            "config_version": 1,
            "links": [],
            "files": {},
            "status": TestStatus.IN_PROGRESS,
            "start": now - timedelta(days=5),
            "end": None,
        },
    ]

    # Insert (skip duplicates)
    counts = {}
    for collection, docs in [("devices", devices), ("drivers", drivers), ("environments", environments), ("tests", tests)]:
        inserted = 0
        for doc in docs:
            if not mongo[collection].find_one({"_id": doc["_id"]}):
                mongo[collection].insert_one(doc)
                inserted += 1
        counts[collection] = inserted

    return {
        "message": "Demo data seeded",
        "devices_created": counts["devices"],
        "drivers_created": counts["drivers"],
        "environments_created": counts["environments"],
        "tests_created": counts["tests"],
    }
