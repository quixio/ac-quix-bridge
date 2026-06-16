"""Cross-reference lookups: drivers, devices, environments."""

from typing import Any

from pymongo.database import Database

from ....models import Device, Driver, Environment


def get_driver(mongo: Database[dict[str, Any]], *, id: str) -> dict[str, Any]:
    doc = mongo.drivers.find_one({"_id": id})
    if not doc:
        raise ValueError(f"Driver {id} not found")
    return Driver(**doc).model_dump(by_alias=False)


def get_device(mongo: Database[dict[str, Any]], *, id: str) -> dict[str, Any]:
    doc = mongo.devices.find_one({"_id": id})
    if not doc:
        raise ValueError(f"Device {id} not found")
    return Device(**doc).model_dump(by_alias=False)


def get_environment(mongo: Database[dict[str, Any]], *, id: str) -> dict[str, Any]:
    doc = mongo.environments.find_one({"_id": id})
    if not doc:
        raise ValueError(f"Environment {id} not found")
    return Environment(**doc).model_dump(by_alias=False)
