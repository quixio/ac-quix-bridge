from datetime import datetime, timezone
from typing import Any
import re

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database
from quixportal import get_filesystem

from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..influx import Influx, get_influx
from ..config_api import get_config_api_client
from ..models import (
    Test,
    TestCreate,
    TestQuery,
    TestUpdate,
    TestFullData,
    File,
    Link,
    LogbookEntry,
    PaginatedResponse,
)
from ..settings import Settings, get_settings

router = APIRouter()


@router.get("/_internal/auth-test")
def auth_test(_: None = Depends(read_permission)) -> dict[str, str]:
    """Simple endpoint to test that authentication is working."""
    return {"status": "success", "message": "Authentication is working!"}


@router.post("/tests", response_model=Test, response_model_by_alias=False)
def create_test(
    test_data: TestCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> Test:
    if mongo.tests.find_one({"_id": test_data.test_id}):
        raise HTTPException(status_code=409, detail="Test with this ID already exists")

    # Validate that at least one Device is provided
    if not test_data.devices:
        raise HTTPException(
            status_code=400,
            detail="At least one Device must be provided",
        )

    # Verify all Devices exist using batch query
    device_ids = [device_ref.device_id for device_ref in test_data.devices]
    existing_devices = set(
        device["_id"] for device in mongo.devices.find({"_id": {"$in": device_ids}}, {"_id": 1})
    )
    missing_devices = set(device_ids) - existing_devices
    if missing_devices:
        raise HTTPException(
            status_code=404,
            detail=f"Devices not found: {', '.join(missing_devices)}",
        )

    response = config_api.post(
        "/api/v1/configurations",
        json={
            "metadata": {"type": "TestConfig", "target_key": test_data.test_id},
            "content": {
                "test_id": test_data.test_id,
                "campaign_id": test_data.campaign_id,
                "devices": [device.model_dump() for device in test_data.devices],
                "environment_id": test_data.environment_id,
                "operator": test_data.operator,
                "sensors": test_data.sensors,
            },
        },
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=424,
            detail=f"Failed to create configuration: {e.response.status_code} {e.response.text}",
        )

    # Extract configuration metadata from response
    config_response = response.json()["data"]
    config_id = config_response["id"]
    config_metadata = config_response["metadata"]

    test = Test(
        _id=test_data.test_id,
        config_id=config_id,
        config_type=config_metadata["type"],
        target_key=config_metadata["target_key"],
        config_version=config_metadata["version"],
        **test_data.model_dump(exclude={"test_id"}),
    )
    mongo.tests.insert_one(test.model_dump(by_alias=True))
    return test


@router.get("/tests", response_model=PaginatedResponse[Test], response_model_by_alias=False)
def list_tests(
    query_params: TestQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Test]:
    """
    Lists tests with pagination and filtering.

    Pagination parameters:
    - page: Page number (default: 1)
    - page_size: Items per page (default: 50, allowed: 25, 50, 100, 200)
    """
    # Extract pagination parameters
    page = query_params.page
    page_size = query_params.page_size

    # Build query excluding pagination params
    query = query_params.model_dump(exclude_none=True, exclude={"q", "device_id", "page", "page_size"})
    if "test_id" in query:
        query["_id"] = query.pop("test_id")

    # Handle device_id filtering (search within devices array)
    if query_params.device_id:
        query["devices.device_id"] = query_params.device_id

    # Enable partial matching for text fields (case-insensitive)
    for field in ["campaign_id", "environment_id", "operator"]:
        if field in query:
            query[field] = {"$regex": re.escape(query[field]), "$options": "i"}

    if query_params.q:
        # Multi-word search: split by spaces and AND all words together
        # Each word can match in any field (words across different fields)
        words = query_params.q.strip().split()

        if words:
            word_conditions = []
            search_fields = ["_id", "campaign_id", "environment_id", "operator", "description"]

            for word in words:
                word_pattern = {"$regex": re.escape(word), "$options": "i"}
                # Each word must match in at least one field
                word_conditions.append({
                    "$or": [{field: word_pattern} for field in search_fields]
                })

            # All words must be found (in any combination of fields)
            if "$and" in query:
                query["$and"].extend(word_conditions)
            else:
                query["$and"] = word_conditions

    # Get total count
    total = mongo.tests.count_documents(query)

    # Apply pagination
    skip = (page - 1) * page_size
    tests = [
        Test(**test)
        for test in mongo.tests.find(query).sort("created_at", -1).skip(skip).limit(page_size)
    ]

    return PaginatedResponse.create(items=tests, total=total, page=page, page_size=page_size)


@router.get("/tests/{test_id}", response_model=Test, response_model_by_alias=False)
def get_test(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Test:
    """
    Retrieves a single test by its test_id.
    """
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")
    return Test(**test)


@router.get("/tests/{test_id}/full", response_model=TestFullData, response_model_by_alias=False)
def get_test_full(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> TestFullData:
    """
    Retrieves a test with all its related data (files, logbook, links) in a single request.

    This endpoint optimizes frontend performance by fetching all test data
    in one API call instead of making 4 separate requests.
    """
    # Get test
    if not (test_doc := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    test = Test(**test_doc)

    # Get files from test document
    files_dict = test_doc.get("files", {})
    files = [File(**file_data) for file_data in files_dict.values()]

    # Get logbook entries
    logbook_docs = mongo.logbook.find({"test_id": test_id}).sort("timestamp", -1)
    logbook = [LogbookEntry(**entry) for entry in logbook_docs]

    # Get links from test document
    links_list = test_doc.get("links", [])
    links = [Link(**link) for link in links_list]

    return TestFullData(
        test=test,
        files=files,
        logbook=logbook,
        links=links,
    )


@router.put("/tests/{test_id}", response_model=Test, response_model_by_alias=False)
def update_test(
    test_id: str,
    test_update: TestUpdate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> Test:
    """
    Updates the metadata of a single test.

    When status transitions to 'in_progress', automatically captures the latest
    Device version for each Device in the test.
    """
    # Get current test to check for status transition
    current_test = mongo.tests.find_one({"_id": test_id})
    if not current_test:
        raise HTTPException(status_code=404, detail="Test not found")

    update_data = test_update.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="At least one field must be provided for update",
        )

    # Validate Devices exist if devices field is being updated using batch query
    if "devices" in update_data and update_data["devices"]:
        device_ids = [device_ref["device_id"] for device_ref in update_data["devices"]]
        existing_devices = set(
            device["_id"] for device in mongo.devices.find({"_id": {"$in": device_ids}}, {"_id": 1})
        )
        missing_devices = set(device_ids) - existing_devices
        if missing_devices:
            raise HTTPException(
                status_code=404,
                detail=f"Devices not found: {', '.join(missing_devices)}",
            )
    elif "devices" in update_data and not update_data["devices"]:
        raise HTTPException(
            status_code=400,
            detail="At least one Device must be provided",
        )

    # Handle status transition to in_progress: capture Device versions
    if (
        update_data.get("status") == "in_progress"
        and current_test.get("status") != "in_progress"
    ):
        # Get Devices from update_data or current test
        devices_to_version = update_data.get("devices", current_test.get("devices", []))

        # Capture latest journal entry for each Device using batch query
        device_ids = [
            device_ref.get("device_id") if isinstance(device_ref, dict) else device_ref.device_id
            for device_ref in devices_to_version
        ]

        # Fetch all latest journal entries in a single aggregation query
        pipeline = [
            {"$match": {"device_id": {"$in": device_ids}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id": "$device_id",
                "latest": {"$first": "$$ROOT"}
            }}
        ]
        journal_map = {
            entry["_id"]: entry["latest"]
            for entry in mongo.device_journal.aggregate(pipeline)
        }

        # Build versioned Devices list using the journal map
        versioned_devices = []
        for device_id in device_ids:
            latest_journal = journal_map.get(device_id)
            versioned_devices.append({
                "device_id": device_id,
                "device_version": latest_journal["_id"] if latest_journal else None
            })

        update_data["devices"] = versioned_devices

    update_data["updated_at"] = datetime.now(timezone.utc)

    updated_test = mongo.tests.find_one_and_update(
        {"_id": test_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )

    if not updated_test:
        raise HTTPException(status_code=404, detail="Test not found")

    test = Test(**updated_test)

    response = config_api.post(
        "/api/v1/configurations",
        json={
            "metadata": {
                "type": test.config_type or "TestConfig",
                "target_key": test.target_key or test.test_id,
            },
            "content": {
                "test_id": test.test_id,
                "campaign_id": test.campaign_id,
                "devices": [device.model_dump() for device in test.devices],
                "environment_id": test.environment_id,
                "operator": test.operator,
                "sensors": test.sensors,
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

    # Extract updated configuration metadata from response
    config_response = response.json()["data"]
    config_id = config_response["id"]
    config_metadata = config_response["metadata"]

    # Update the test with new config metadata
    # Note: config_id might change if a new configuration was created (admin-generated tests)
    mongo.tests.update_one(
        {"_id": test_id},
        {"$set": {
            "config_id": config_id,
            "config_type": config_metadata["type"],
            "target_key": config_metadata["target_key"],
            "config_version": config_metadata["version"],
        }}
    )

    # Refresh test from DB to get updated values
    updated_test = mongo.tests.find_one({"_id": test_id})
    test = Test(**updated_test)

    return test


@router.delete("/tests/{test_id}", status_code=204)
def delete_test(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    fs: Any = Depends(get_filesystem),
    settings: Settings = Depends(get_settings),
    influx: Influx = Depends(get_influx),
    config_api: httpx.Client = Depends(get_config_api_client),
    _: None = Depends(update_permission),
) -> None:
    """
    Deletes a single test by its test_id.
    """
    # Get the test to find associated files
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    # Delete configuration from Config API
    response = config_api.delete(f"/api/v1/configurations/{test['config_id']}")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=424,
            detail=f"Failed to delete configuration: {e.response.status_code} {e.response.text}",
        )

    # Delete all files from blob storage
    files = test.get("files", {})
    for file in files.values():
        path = f"{settings.workspace_id}/test-manager/{test_id}/{file['name']}"
        try:
            fs.rm_file(path)
        except FileNotFoundError:
            pass

    # Delete logbook entries from InfluxDB
    logbook_entries = list(mongo.logbook.find({"test_id": test_id}))
    for entry in logbook_entries:
        influx.logbook.delete(entry["_id"])

    # Delete logbook entries and test from MongoDB
    mongo.logbook.delete_many({"test_id": test_id})
    mongo.tests.delete_one({"_id": test_id})


@router.get("/tests/filters/campaign-ids", response_model=list[str])
def get_campaign_ids(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[str]:
    """Get distinct campaign IDs for filter autocomplete."""
    campaign_ids = mongo.tests.distinct("campaign_id")
    return sorted([cid for cid in campaign_ids if cid])


@router.get("/tests/filters/environment-ids", response_model=list[str])
def get_environment_ids(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[str]:
    """Get distinct Environment IDs for filter autocomplete."""
    environment_ids = mongo.tests.distinct("environment_id")
    return sorted([eid for eid in environment_ids if eid])


@router.get("/tests/filters/operators", response_model=list[str])
def get_operators(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[str]:
    """Get distinct operators for filter autocomplete."""
    operators = mongo.tests.distinct("operator")
    return sorted([op for op in operators if op])
