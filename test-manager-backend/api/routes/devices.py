import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..models import (
    Device,
    DeviceCreate,
    DeviceQuery,
    DeviceUpdate,
    PaginatedResponse,
)
from ..utils import now

router = APIRouter()


def generate_device_id(mongo: Database[dict[str, Any]]) -> str:
    """Generate the next auto-incremented device ID (DEV-0001, DEV-0002, etc.)."""
    last = mongo.devices.find_one(
        {"_id": {"$regex": r"^DEV-\d+$"}},
        sort=[("_id", -1)],
    )
    if last:
        last_num = int(last["_id"].split("-")[1])
        return f"DEV-{last_num + 1:04d}"
    return "DEV-0001"


@router.post("/devices", response_model=Device, response_model_by_alias=False)
def create_device(
    device_data: DeviceCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Device:
    """Create a new Device with an auto-generated ID."""
    device_id = generate_device_id(mongo)

    device = Device(
        _id=device_id,
        category=device_data.category,
        name=device_data.name,
        status=device_data.status,
    )
    mongo.devices.insert_one(device.model_dump(by_alias=True))
    return device


@router.get("/devices", response_model=PaginatedResponse[Device], response_model_by_alias=False)
def list_devices(
    query_params: DeviceQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Device]:
    """List Devices with pagination and filtering."""
    page = query_params.page
    page_size = query_params.page_size

    query: dict[str, Any] = {}

    if query_params.category:
        query["category"] = query_params.category.value

    if query_params.status:
        query["status"] = query_params.status.value

    if query_params.q:
        words = query_params.q.strip().split()
        if words:
            word_conditions = []
            for word in words:
                word_pattern = {"$regex": re.escape(word), "$options": "i"}
                word_conditions.append({
                    "$or": [
                        {"_id": word_pattern},
                        {"name": word_pattern},
                        {"category": word_pattern},
                    ]
                })
            query["$and"] = word_conditions

    total = mongo.devices.count_documents(query)
    skip = (page - 1) * page_size
    devices = [
        Device(**d)
        for d in mongo.devices.find(query).sort("_id", 1).skip(skip).limit(page_size)
    ]
    return PaginatedResponse.create(items=devices, total=total, page=page, page_size=page_size)


@router.get("/devices/{device_id}", response_model=Device, response_model_by_alias=False)
def get_device(
    device_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Device:
    """Get a single Device by ID."""
    doc = mongo.devices.find_one({"_id": device_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Device not found")
    return Device(**doc)


@router.post("/devices/batch", response_model=list[Device], response_model_by_alias=False)
def get_devices_batch(
    device_ids: list[str] = Body(..., description="List of Device IDs to fetch"),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[Device]:
    """Retrieve multiple Devices in a single request."""
    if not device_ids:
        return []
    devices = mongo.devices.find({"_id": {"$in": device_ids}})
    return [Device(**d) for d in devices]


@router.put("/devices/{device_id}", response_model=Device, response_model_by_alias=False)
def update_device(
    device_id: str,
    device_data: DeviceUpdate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Device:
    """Update an existing Device."""
    update_fields = device_data.model_dump(exclude_none=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_fields["updated_at"] = now()

    result = mongo.devices.find_one_and_update(
        {"_id": device_id},
        {"$set": update_fields},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Device not found")
    return Device(**result)


@router.delete("/devices/{device_id}")
def delete_device(
    device_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    """Delete a Device. Prevents deletion if referenced by tests."""
    if not mongo.devices.find_one({"_id": device_id}):
        raise HTTPException(status_code=404, detail="Device not found")

    # Check if referenced by tests
    referencing = list(mongo.tests.find({"devices.device_id": device_id}, {"_id": 1}))
    if referencing:
        test_ids = [t["_id"] for t in referencing[:5]]
        more = f" and {len(referencing) - 5} more" if len(referencing) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: referenced by test(s): {', '.join(test_ids)}{more}",
        )

    mongo.device_journal.delete_many({"device_id": device_id})
    mongo.devices.delete_one({"_id": device_id})
    return {"message": f"Device {device_id} deleted"}
