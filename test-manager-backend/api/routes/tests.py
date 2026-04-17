from datetime import datetime, timezone
from typing import Any
import re

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database
from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..config_api import get_config_api_client
from ..models import (
    Test,
    TestCreate,
    TestQuery,
    TestUpdate,
    TestFullData,
    SessionInfo,
    File,
    Link,
    LogbookEntry,
    PaginatedResponse,
)

router = APIRouter()


def generate_test_id(mongo: Database[dict[str, Any]]) -> str:
    """Generate the next auto-incremented test ID (TST-0001, TST-0002, etc.)."""
    last = mongo.tests.find_one(
        {"_id": {"$regex": r"^TST-\d+$"}},
        sort=[("_id", -1)],
    )
    if last:
        last_num = int(last["_id"].split("-")[1])
        return f"TST-{last_num + 1:04d}"
    return "TST-0001"


def resolve_test_names(test: Test, mongo: Database[dict[str, Any]]) -> Test:
    """Populate resolved display names on a Test object."""
    pc = mongo.devices.find_one({"_id": test.pc_device_id}, {"name": 1})
    rig = mongo.devices.find_one({"_id": test.test_rig_device_id}, {"name": 1})
    env = mongo.environments.find_one({"_id": test.environment_id}, {"name": 1})
    test.pc_device_name = pc["name"] if pc else None
    test.test_rig_device_name = rig["name"] if rig else None
    test.environment_name = env["name"] if env else None
    return test


def resolve_tests_names(
    tests: list[Test], mongo: Database[dict[str, Any]]
) -> list[Test]:
    """Batch-resolve display names for a list of Tests."""
    if not tests:
        return tests

    # Collect all unique IDs
    device_ids = set()
    env_ids = set()
    for t in tests:
        device_ids.add(t.pc_device_id)
        device_ids.add(t.test_rig_device_id)
        env_ids.add(t.environment_id)

    # Batch fetch
    device_map = {
        d["_id"]: d["name"]
        for d in mongo.devices.find({"_id": {"$in": list(device_ids)}}, {"name": 1})
    }
    env_map = {
        e["_id"]: e["name"]
        for e in mongo.environments.find({"_id": {"$in": list(env_ids)}}, {"name": 1})
    }

    for t in tests:
        t.pc_device_name = device_map.get(t.pc_device_id)
        t.test_rig_device_name = device_map.get(t.test_rig_device_id)
        t.environment_name = env_map.get(t.environment_id)

    return tests


@router.get("/_internal/auth-test")
def auth_test(_: None = Depends(read_permission)) -> dict[str, str]:
    return {"status": "success", "message": "Authentication is working!"}


@router.post("/tests", response_model=Test, response_model_by_alias=False)
def create_test(
    test_data: TestCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> Test:
    """Create a new Test with auto-generated ID. Sends config to Dynamic Config Manager."""
    test_id = generate_test_id(mongo)

    # Verify devices exist
    device_ids = [test_data.pc_device_id, test_data.test_rig_device_id]
    existing = set(
        d["_id"] for d in mongo.devices.find({"_id": {"$in": device_ids}}, {"_id": 1})
    )
    missing = set(device_ids) - existing
    if missing:
        raise HTTPException(
            status_code=404, detail=f"Devices not found: {', '.join(missing)}"
        )

    # Get PC device name for target_key
    pc_device = mongo.devices.find_one({"_id": test_data.pc_device_id})
    pc_hostname = pc_device["name"] if pc_device else test_data.pc_device_id

    # Get test rig device name
    rig_device = mongo.devices.find_one({"_id": test_data.test_rig_device_id})
    rig_name = (
        rig_device["name"].lower().replace(" ", "_")
        if rig_device
        else test_data.test_rig_device_id
    )

    # Get environment name
    env = mongo.environments.find_one({"_id": test_data.environment_id})
    env_name = (
        env["name"].lower().replace(" ", "_").replace("'", "")
        if env
        else test_data.environment_id
    )

    # Send config to Dynamic Config Manager
    valid_from = datetime.now(timezone.utc).isoformat()
    response = config_api.post(
        "/api/v1/configurations",
        json={
            "metadata": {
                "type": "experiment",
                "target_key": pc_hostname,
                "category": "ac-telemetry",
                "valid_from": valid_from,
            },
            "content": {
                "test_id": test_id,
                "environment": env_name,
                "test_rig": rig_name,
                "experiment_id": test_data.experiment_id,
                "driver": test_data.driver.lower(),
                "requirements": test_data.requirements,
            },
            "replace": True,
        },
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=424,
            detail=f"Failed to create configuration: {e.response.status_code} {e.response.text}",
        )

    config_response = response.json()["data"]
    config_id = config_response["id"]
    config_metadata = config_response["metadata"]

    test = Test(
        _id=test_id,
        config_id=config_id,
        config_type=config_metadata["type"],
        target_key=config_metadata["target_key"],
        config_version=config_metadata["version"],
        **test_data.model_dump(),
    )
    mongo.tests.insert_one(
        test.model_dump(
            by_alias=True,
            exclude={"pc_device_name", "test_rig_device_name", "environment_name"},
        )
    )
    return resolve_test_names(test, mongo)


@router.get(
    "/tests", response_model=PaginatedResponse[Test], response_model_by_alias=False
)
def list_tests(
    query_params: TestQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Test]:
    """List Tests with pagination and filtering."""
    page = query_params.page
    page_size = query_params.page_size

    query: dict[str, Any] = {}

    if query_params.experiment_id:
        query["experiment_id"] = {
            "$regex": re.escape(query_params.experiment_id),
            "$options": "i",
        }

    if query_params.environment_id:
        query["environment_id"] = query_params.environment_id

    if query_params.driver:
        query["driver"] = {"$regex": re.escape(query_params.driver), "$options": "i"}

    if query_params.q:
        words = query_params.q.strip().split()
        if words:
            word_conditions = []
            search_fields = ["_id", "experiment_id", "environment_id", "driver"]
            for word in words:
                word_pattern = {"$regex": re.escape(word), "$options": "i"}
                word_conditions.append(
                    {"$or": [{field: word_pattern} for field in search_fields]}
                )
            query["$and"] = word_conditions

    total = mongo.tests.count_documents(query)
    skip = (page - 1) * page_size
    tests = [
        Test(**t)
        for t in mongo.tests.find(query).sort("_id", 1).skip(skip).limit(page_size)
    ]
    resolve_tests_names(tests, mongo)
    return PaginatedResponse.create(
        items=tests, total=total, page=page, page_size=page_size
    )


@router.get("/tests/{test_id}", response_model=Test, response_model_by_alias=False)
def get_test(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Test:
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")
    return resolve_test_names(Test(**test), mongo)


@router.get(
    "/tests/{test_id}/full", response_model=TestFullData, response_model_by_alias=False
)
def get_test_full(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> TestFullData:
    if not (test_doc := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    test = resolve_test_names(Test(**test_doc), mongo)
    files_dict = test_doc.get("files", {})
    files = [File(**f) for f in files_dict.values()]
    logbook = [
        LogbookEntry(**e)
        for e in mongo.logbook.find({"test_id": test_id}).sort("timestamp", -1)
    ]
    links = [Link(**link) for link in test_doc.get("links", [])]

    return TestFullData(test=test, files=files, logbook=logbook, links=links)


@router.put("/tests/{test_id}", response_model=Test, response_model_by_alias=False)
def update_test(
    test_id: str,
    test_update: TestUpdate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> Test:
    """Update a Test and sync config to Dynamic Config Manager."""
    current_test = mongo.tests.find_one({"_id": test_id})
    if not current_test:
        raise HTTPException(status_code=404, detail="Test not found")

    update_data = test_update.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Verify devices if changed
    for field in ["pc_device_id", "test_rig_device_id"]:
        if field in update_data:
            if not mongo.devices.find_one({"_id": update_data[field]}):
                raise HTTPException(
                    status_code=404, detail=f"Device not found: {update_data[field]}"
                )

    update_data["updated_at"] = datetime.now(timezone.utc)

    updated_test = mongo.tests.find_one_and_update(
        {"_id": test_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )
    if not updated_test:
        raise HTTPException(status_code=404, detail="Test not found")

    test = Test(**updated_test)

    # Rebuild config payload
    pc_device = mongo.devices.find_one({"_id": test.pc_device_id})
    pc_hostname = pc_device["name"] if pc_device else test.pc_device_id
    rig_device = mongo.devices.find_one({"_id": test.test_rig_device_id})
    rig_name = (
        rig_device["name"].lower().replace(" ", "_")
        if rig_device
        else test.test_rig_device_id
    )
    env = mongo.environments.find_one({"_id": test.environment_id})
    env_name = (
        env["name"].lower().replace(" ", "_").replace("'", "")
        if env
        else test.environment_id
    )

    valid_from = datetime.now(timezone.utc).isoformat()
    response = config_api.post(
        "/api/v1/configurations",
        json={
            "metadata": {
                "type": "experiment",
                "target_key": pc_hostname,
                "category": "ac-telemetry",
                "valid_from": valid_from,
            },
            "content": {
                "test_id": test.test_id,
                "environment": env_name,
                "test_rig": rig_name,
                "experiment_id": test.experiment_id,
                "driver": test.driver.lower(),
                "requirements": test.requirements,
            },
            "replace": True,
        },
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=424,
            detail=f"Failed to update configuration: {e.response.status_code} {e.response.text}",
        )

    config_response = response.json()["data"]
    mongo.tests.update_one(
        {"_id": test_id},
        {
            "$set": {
                "config_id": config_response["id"],
                "config_type": config_response["metadata"]["type"],
                "target_key": config_response["metadata"]["target_key"],
                "config_version": config_response["metadata"]["version"],
            }
        },
    )

    return resolve_test_names(Test(**mongo.tests.find_one({"_id": test_id})), mongo)


@router.get("/tests/{test_id}/telemetry-params", response_model_by_alias=False)
def get_telemetry_params(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(read_permission),
):
    """Get Quix Lake partition parameters for a test by fetching its config content.

    Returns the exact values stored in the Dynamic Config Manager, which match
    the Hive partition columns in Quix Lake (environment, test_rig, experiment, driver).
    Also includes hardcoded track and carModel for now.
    """
    test = mongo.tests.find_one({"_id": test_id})
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    config_id = test.get("config_id")
    config_version = test.get("config_version")
    if not config_id or not config_version:
        raise HTTPException(status_code=404, detail="Test has no configuration")

    try:
        resp = config_api.get(
            f"/api/v1/configurations/{config_id}/versions/{config_version}/content"
        )
        resp.raise_for_status()
        content = resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=424,
            detail=f"Failed to fetch config content: {e.response.status_code}",
        )

    # Get track/carModel from sessions if available, otherwise fallback
    sessions = test.get("sessions", [])
    track = sessions[0]["track"] if sessions else "ks_nurburgring"
    car_model = sessions[0]["car_model"] if sessions else "bmw_1m"

    return {
        "environment": content.get("environment", ""),
        "test_rig": content.get("test_rig", ""),
        "experiment": content.get("experiment_id", ""),
        "driver": content.get("driver", ""),
        "track": track,
        "carModel": car_model,
        "session_ids": [s["session_id"] for s in sessions],
    }


@router.post(
    "/tests/{test_id}/sessions", response_model=Test, response_model_by_alias=False
)
def add_session(
    test_id: str,
    session: SessionInfo = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Test:
    """Add a session to a test. Skips if session_id already exists."""
    test = mongo.tests.find_one({"_id": test_id})
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    # Check for duplicate session_id
    existing_ids = [s["session_id"] for s in test.get("sessions", [])]
    if session.session_id in existing_ids:
        return resolve_test_names(Test(**test), mongo)

    # Append session
    mongo.tests.update_one(
        {"_id": test_id},
        {"$push": {"sessions": session.model_dump()}},
    )

    updated = mongo.tests.find_one({"_id": test_id})
    return resolve_test_names(Test(**updated), mongo)


@router.post(
    "/tests/{test_id}/activate", response_model=Test, response_model_by_alias=False
)
def activate_test(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> Test:
    """Push the test's current content as a new DCM version.

    No content changes. The new version becomes the latest for this hostname,
    making this test the one the AC bridge enriches telemetry against.
    Sibling tests on the same hostname are untouched.
    """
    current = mongo.tests.find_one({"_id": test_id})
    if not current:
        raise HTTPException(status_code=404, detail="Test not found")

    test = Test(**current)

    pc_device = mongo.devices.find_one({"_id": test.pc_device_id})
    pc_hostname = pc_device["name"] if pc_device else test.pc_device_id
    rig_device = mongo.devices.find_one({"_id": test.test_rig_device_id})
    rig_name = (
        rig_device["name"].lower().replace(" ", "_")
        if rig_device
        else test.test_rig_device_id
    )
    env = mongo.environments.find_one({"_id": test.environment_id})
    env_name = (
        env["name"].lower().replace(" ", "_").replace("'", "")
        if env
        else test.environment_id
    )

    valid_from = datetime.now(timezone.utc).isoformat()
    response = config_api.post(
        "/api/v1/configurations",
        json={
            "metadata": {
                "type": "experiment",
                "target_key": pc_hostname,
                "category": "ac-telemetry",
                "valid_from": valid_from,
            },
            "content": {
                "test_id": test.test_id,
                "environment": env_name,
                "test_rig": rig_name,
                "experiment_id": test.experiment_id,
                "driver": test.driver.lower(),
                "requirements": test.requirements,
            },
            "replace": True,
        },
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=424,
            detail=f"Failed to activate configuration: {e.response.status_code} {e.response.text}",
        )

    config_response = response.json()["data"]
    updated = mongo.tests.find_one_and_update(
        {"_id": test_id},
        {
            "$set": {
                "config_id": config_response["id"],
                "config_version": config_response["metadata"]["version"],
                "updated_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    assert updated is not None  # guarded by the find_one at the top
    return resolve_test_names(Test(**updated), mongo)


@router.delete("/tests/{test_id}", status_code=204)
def delete_test(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> None:
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    # Delete only this test's version of the experiment config. Other tests on
    # the same hostname share the same config_id (one config per target_key,
    # one version per test), so deleting the whole config would wipe their history.
    try:
        config_api.delete(
            f"/api/v1/configurations/{test['config_id']}/versions/{test['config_version']}"
        ).raise_for_status()
    except httpx.HTTPStatusError:
        pass  # Version may already be deleted

    mongo.logbook.delete_many({"test_id": test_id})
    mongo.tests.delete_one({"_id": test_id})


@router.get("/tests/filters/experiment-ids", response_model=list[str])
def get_experiment_ids(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[str]:
    """Get distinct experiment IDs for filter autocomplete."""
    ids = mongo.tests.distinct("experiment_id")
    return sorted([i for i in ids if i])


@router.get("/tests/filters/environment-ids", response_model=list[str])
def get_environment_ids(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[str]:
    ids = mongo.tests.distinct("environment_id")
    return sorted([i for i in ids if i])


@router.get("/tests/filters/drivers", response_model=list[str])
def get_drivers(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[str]:
    drivers = mongo.tests.distinct("driver")
    return sorted([d for d in drivers if d])
