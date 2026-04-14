import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..models import (
    Environment,
    EnvironmentCreate,
    EnvironmentQuery,
    EnvironmentUpdate,
    PaginatedResponse,
)
from ..utils import now

router = APIRouter()


def generate_environment_id(mongo: Database[dict[str, Any]]) -> str:
    """Generate the next auto-incremented environment ID (ENV-0001, ENV-0002, etc.)."""
    last = mongo.environments.find_one(
        {"_id": {"$regex": r"^ENV-\d+$"}},
        sort=[("_id", -1)],
    )
    if last:
        last_num = int(last["_id"].split("-")[1])
        return f"ENV-{last_num + 1:04d}"
    return "ENV-0001"


@router.post("/environments", response_model=Environment, response_model_by_alias=False)
def create_environment(
    env_data: EnvironmentCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Environment:
    """Create a new Environment with an auto-generated ID."""
    env_id = generate_environment_id(mongo)

    environment = Environment(
        _id=env_id,
        name=env_data.name,
        location=env_data.location,
        status=env_data.status,
    )
    mongo.environments.insert_one(environment.model_dump(by_alias=True))
    return environment


@router.get("/environments", response_model=PaginatedResponse[Environment], response_model_by_alias=False)
def list_environments(
    query_params: EnvironmentQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Environment]:
    """List Environments with pagination and filtering."""
    page = query_params.page
    page_size = query_params.page_size

    query: dict[str, Any] = {}

    if query_params.name:
        query["name"] = {"$regex": re.escape(query_params.name), "$options": "i"}

    if query_params.location:
        query["location"] = {"$regex": re.escape(query_params.location), "$options": "i"}

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
                        {"location": word_pattern},
                    ]
                })
            query["$and"] = word_conditions

    total = mongo.environments.count_documents(query)
    skip = (page - 1) * page_size
    environments = [
        Environment(**d)
        for d in mongo.environments.find(query).sort("_id", 1).skip(skip).limit(page_size)
    ]
    return PaginatedResponse.create(items=environments, total=total, page=page, page_size=page_size)


@router.get("/environments/{environment_id}", response_model=Environment, response_model_by_alias=False)
def get_environment(
    environment_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Environment:
    """Get a single Environment by ID."""
    doc = mongo.environments.find_one({"_id": environment_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Environment not found")
    return Environment(**doc)


@router.put("/environments/{environment_id}", response_model=Environment, response_model_by_alias=False)
def update_environment(
    environment_id: str,
    env_data: EnvironmentUpdate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Environment:
    """Update an existing Environment."""
    update_fields = env_data.model_dump(exclude_none=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_fields["updated_at"] = now()

    result = mongo.environments.find_one_and_update(
        {"_id": environment_id},
        {"$set": update_fields},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Environment not found")
    return Environment(**result)


@router.delete("/environments/{environment_id}")
def delete_environment(
    environment_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    """Delete an Environment."""
    result = mongo.environments.delete_one({"_id": environment_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Environment not found")
    return {"message": f"Environment {environment_id} deleted"}
