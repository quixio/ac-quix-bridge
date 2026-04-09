import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..models import (
    Driver,
    DriverCreate,
    DriverQuery,
    DriverUpdate,
    PaginatedResponse,
)
from ..utils import now

router = APIRouter()


def generate_driver_id(mongo: Database[dict[str, Any]]) -> str:
    """Generate the next auto-incremented driver ID (DRV-0001, DRV-0002, etc.)."""
    last = mongo.drivers.find_one(
        {"_id": {"$regex": r"^DRV-\d+$"}},
        sort=[("_id", -1)],
    )
    if last:
        last_num = int(last["_id"].split("-")[1])
        return f"DRV-{last_num + 1:04d}"
    return "DRV-0001"


@router.post("/drivers", response_model=Driver, response_model_by_alias=False)
def create_driver(
    driver_data: DriverCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Driver:
    """Create a new Driver with an auto-generated ID."""
    driver_id = generate_driver_id(mongo)

    driver = Driver(
        _id=driver_id,
        name=driver_data.name,
    )
    mongo.drivers.insert_one(driver.model_dump(by_alias=True))
    return driver


@router.get("/drivers", response_model=PaginatedResponse[Driver], response_model_by_alias=False)
def list_drivers(
    query_params: DriverQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Driver]:
    """List Drivers with pagination and filtering."""
    page = query_params.page
    page_size = query_params.page_size

    query: dict[str, Any] = {}

    if query_params.name:
        query["name"] = {"$regex": re.escape(query_params.name), "$options": "i"}

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
                    ]
                })
            query["$and"] = word_conditions

    total = mongo.drivers.count_documents(query)
    skip = (page - 1) * page_size
    drivers = [
        Driver(**d)
        for d in mongo.drivers.find(query).sort("_id", 1).skip(skip).limit(page_size)
    ]
    return PaginatedResponse.create(items=drivers, total=total, page=page, page_size=page_size)


@router.get("/drivers/{driver_id}", response_model=Driver, response_model_by_alias=False)
def get_driver(
    driver_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Driver:
    """Get a single Driver by ID."""
    doc = mongo.drivers.find_one({"_id": driver_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Driver not found")
    return Driver(**doc)


@router.put("/drivers/{driver_id}", response_model=Driver, response_model_by_alias=False)
def update_driver(
    driver_id: str,
    driver_data: DriverUpdate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Driver:
    """Update an existing Driver."""
    update_fields = driver_data.model_dump(exclude_none=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_fields["updated_at"] = now()

    result = mongo.drivers.find_one_and_update(
        {"_id": driver_id},
        {"$set": update_fields},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Driver not found")
    return Driver(**result)


@router.delete("/drivers/{driver_id}")
def delete_driver(
    driver_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    """Delete a Driver."""
    result = mongo.drivers.delete_one({"_id": driver_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Driver not found")
    return {"message": f"Driver {driver_id} deleted"}
