from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import re

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..models import (
    Device,
    DeviceCreate,
    DeviceQuery,
    DeviceUpdate,
    DeviceJournalEntry,
    DeviceJournalEntrySummary,
    DeviceJournalEntryCreate,
    DeviceUpdatePreview,
    JournalCategory,
    PaginatedResponse,
)

router = APIRouter()


def derive_sample_id(sample_type: str, sample_nr: str | None) -> str:
    """Derive sample_id from sample_type and sample_nr.

    Returns:
        {sample_type} if sample_nr is None or empty
        {sample_type}-{sample_nr} if sample_nr is present
    """
    if sample_nr:
        return f"{sample_type}-{sample_nr}"
    return sample_type


def generate_change_description(
    old_device: dict[str, Any], new_data: dict[str, Any]
) -> tuple[str, list[str]]:
    """Generate a human-readable description of changes between old and new Device data.

    Uses a blacklist approach - detects changes in all fields except those explicitly excluded.

    Args:
        old_device: Current Device data from database
        new_data: Proposed update data (only changed fields)

    Returns:
        Tuple of (description_text, list_of_changed_field_names)
    """
    changes = []
    changed_fields = []

    # Fields to exclude from change detection (metadata, internal fields)
    excluded_fields = {
        "journal_text",
        "journal_category",
        "updated_at",
        "created_at",
        "_id",
    }

    # Field name mapping for better readability (optional - for nice labels)
    field_labels = {
        "product_key": "Product Key",
        "status": "Status",
        "status_note": "Status Note",
        "location": "Location",
        "project": "Project",
        "picture_link": "Picture Link",
        "software_bundle": "Software Bundle",
        "comment": "Comment",
        "sample_owner": "Sample Owner",
        "last_editor": "Last Editor",
        "device_id": "Device ID",
        "manufacturer": "Manufacturer",
        "product_category": "Product Category",
        "product_name": "Product Name",
        "product_type": "Product Type",
        "product_variant": "Product Variant",
        "sample_type": "Sample Type",
        "sample_nr": "Sample Number",
        "sample_id": "Sample ID",
        "hardware_link": "Hardware Link",
        "creator": "Creator",
    }

    # Check all fields in new_data (except excluded ones)
    for field, new_value in new_data.items():
        if field in excluded_fields:
            continue

        old_value = old_device.get(field)
        if old_value != new_value:
            # Get human-readable label or use field name with title case
            label = field_labels.get(field, field.replace("_", " ").title())
            old_str = str(old_value) if old_value is not None else "None"
            new_str = str(new_value) if new_value is not None else "None"
            changes.append(f"{label} ({old_str} → {new_str})")
            changed_fields.append(field)

    if not changes:
        return "No significant changes detected", []

    description = "Updated: " + ", ".join(changes)
    return description, changed_fields


def create_journal_entry(
    mongo: Database[dict[str, Any]],
    device_id: str,
    editor: str,
    text: str,
    data: dict[str, Any],
    category: JournalCategory | None = None,
) -> DeviceJournalEntry:
    """Create a journal entry for a Device.

    Args:
        mongo: MongoDB database instance
        device_id: Device identifier
        editor: User making the change
        text: Description of the change
        data: Full JSON snapshot of the Device
        category: Optional journal category

    Returns:
        The created DeviceJournalEntry
    """
    journal_entry = DeviceJournalEntry(
        _id=str(uuid4()),
        device_id=device_id,
        editor=editor,
        text=text,
        data=data,
        category=category,
        timestamp=datetime.now(timezone.utc),
    )
    mongo.device_journal.insert_one(journal_entry.model_dump(by_alias=True))
    return journal_entry


@router.post("/devices", response_model=Device, response_model_by_alias=False)
def create_device(
    device_data: DeviceCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Device:
    """Create a new Device.

    Automatically derives sample_id from sample_type and sample_nr.
    Creates an initial journal entry with the Device creation snapshot.
    """
    # Check if Device already exists
    if mongo.devices.find_one({"_id": device_data.device_id}):
        raise HTTPException(status_code=409, detail="Device with this ID already exists")

    # Derive sample_id
    sample_id = derive_sample_id(device_data.sample_type, device_data.sample_nr)

    # Extract journal metadata before creating Device
    journal_text = device_data.journal_text or f"Device created: {sample_id}"
    journal_category = device_data.journal_category or JournalCategory.SETUP

    # Create Device instance (exclude journal fields - they're not part of Device schema)
    device = Device(
        _id=device_data.device_id,
        sample_id=sample_id,
        last_editor=device_data.creator,
        **device_data.model_dump(exclude={"device_id", "journal_text", "journal_category"}),
    )

    # Insert Device into database
    mongo.devices.insert_one(device.model_dump(by_alias=True))

    # Create initial journal entry with custom text if provided
    create_journal_entry(
        mongo=mongo,
        device_id=device.device_id,
        editor=device.creator,
        text=journal_text,
        data=device.model_dump(by_alias=True),
        category=journal_category,
    )

    return device


@router.get("/devices", response_model=PaginatedResponse[Device], response_model_by_alias=False)
def list_devices(
    query_params: DeviceQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Device]:
    """List Devices with pagination and filtering.

    Supports filtering by device_id, status, manufacturer, product_category,
    product_name, sample_type, sample_id, location, project, creator.
    Also supports text search across multiple fields using the 'q' parameter.

    Pagination parameters:
    - page: Page number (default: 1)
    - page_size: Items per page (default: 50, allowed: 25, 50, 100, 200)
    """
    # Extract pagination parameters
    page = query_params.page
    page_size = query_params.page_size

    # Build query excluding pagination params
    query = query_params.model_dump(exclude_none=True, exclude={"q", "id_search", "page", "page_size"})
    if "device_id" in query:
        query["_id"] = query.pop("device_id")

    # Enable partial matching for text fields (case-insensitive)
    for field in ["location", "product_category", "product_name", "manufacturer", "sample_id", "project", "creator"]:
        if field in query:
            query[field] = {"$regex": re.escape(query[field]), "$options": "i"}

    # ID search: only search Device ID and Sample ID (quick search from test form)
    if query_params.id_search:
        words = query_params.id_search.strip().split()

        if words:
            word_conditions = []
            search_fields = ["_id", "sample_id"]  # Only Device ID and Sample ID

            for word in words:
                word_pattern = {"$regex": re.escape(word), "$options": "i"}
                word_conditions.append({
                    "$or": [{field: word_pattern} for field in search_fields]
                })

            if "$and" in query:
                query["$and"].extend(word_conditions)
            else:
                query["$and"] = word_conditions

    # Full text search: search across all fields (advanced filters dialog)
    if query_params.q:
        # Multi-word search: split by spaces and AND all words together
        # Each word can match in any field (words across different fields)
        words = query_params.q.strip().split()

        if words:
            word_conditions = []
            search_fields = ["_id", "manufacturer", "product_category", "product_name", "sample_id", "location"]

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
    total = mongo.devices.count_documents(query)

    # Apply pagination
    skip = (page - 1) * page_size
    devices = [
        Device(**device)
        for device in mongo.devices.find(query).sort("created_at", -1).skip(skip).limit(page_size)
    ]

    return PaginatedResponse.create(items=devices, total=total, page=page, page_size=page_size)


@router.get("/devices/{device_id}", response_model=Device, response_model_by_alias=False)
def get_device(
    device_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Device:
    """Retrieve a single Device by its device_id."""
    if not (device := mongo.devices.find_one({"_id": device_id})):
        raise HTTPException(status_code=404, detail="Device not found")
    return Device(**device)


@router.post("/devices/batch", response_model=list[Device], response_model_by_alias=False)
def get_devices_batch(
    device_ids: list[str] = Body(..., description="List of Device IDs to fetch"),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[Device]:
    """Retrieve multiple Devices in a single request.

    This endpoint optimizes frontend performance by fetching multiple Devices
    in one API call instead of making N separate requests.
    """
    if not device_ids:
        return []

    devices = mongo.devices.find({"_id": {"$in": device_ids}})
    return [Device(**device) for device in devices]


@router.post(
    "/devices/{device_id}/preview-update",
    response_model=DeviceUpdatePreview,
    response_model_by_alias=False,
)
def preview_device_update(
    device_id: str,
    device_update: DeviceUpdate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> DeviceUpdatePreview:
    """Preview a Device update to see suggested journal text.

    This endpoint does NOT modify the Device. It only returns a suggested
    journal entry text based on the detected changes.
    """
    # Get current Device
    if not (current_device := mongo.devices.find_one({"_id": device_id})):
        raise HTTPException(status_code=404, detail="Device not found")

    # Get only the fields that are being updated
    update_data = device_update.model_dump(exclude_unset=True, exclude={"journal_text", "journal_category"})

    # Generate suggested text
    suggested_text, changed_fields = generate_change_description(current_device, update_data)

    return DeviceUpdatePreview(
        suggested_text=suggested_text,
        changed_fields=changed_fields,
    )


@router.put("/devices/{device_id}", response_model=Device, response_model_by_alias=False)
def update_device(
    device_id: str,
    device_update: DeviceUpdate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Device:
    """Update a Device.

    All Device fields (except _id, created_at, updated_at, creator) can be updated.
    Field-level immutability restrictions can be enforced in the frontend if needed.

    Automatically creates a journal entry with the updated Device snapshot.
    """
    # Get current Device
    current_device = mongo.devices.find_one({"_id": device_id})
    if not current_device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Extract journal metadata before processing update
    journal_text = device_update.journal_text
    journal_category = device_update.journal_category

    # Get update data excluding journal fields
    update_data = device_update.model_dump(exclude_unset=True, exclude={"journal_text", "journal_category"})

    # Filter out None values to prevent overwriting existing data with null
    # This ensures that only fields with actual values are updated
    update_data = {k: v for k, v in update_data.items() if v is not None}

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="At least one field must be provided for update",
        )

    # Recalculate sample_id if sample_type or sample_nr changed
    if "sample_type" in update_data or "sample_nr" in update_data:
        # Get the final values (use updated value if provided, otherwise keep current)
        final_sample_type = update_data.get("sample_type", current_device.get("sample_type"))
        final_sample_nr = update_data.get("sample_nr", current_device.get("sample_nr"))
        # Derive new sample_id
        update_data["sample_id"] = derive_sample_id(final_sample_type, final_sample_nr)

    # Set updated_at timestamp
    update_data["updated_at"] = datetime.now(timezone.utc)

    # Update Device in database
    updated_device = mongo.devices.find_one_and_update(
        {"_id": device_id},
        {"$set": update_data},
        return_document=ReturnDocument.AFTER,
    )

    if not updated_device:
        raise HTTPException(status_code=404, detail="Device not found")

    device = Device(**updated_device)

    # Create journal entry for the update
    # Use provided journal text or default to simple message
    editor = device_update.last_editor or device.last_editor
    journal_text_final = journal_text if journal_text else "Device updated"

    create_journal_entry(
        mongo=mongo,
        device_id=device.device_id,
        editor=editor,
        text=journal_text_final,
        data=device.model_dump(by_alias=True),
        category=journal_category,
    )

    return device


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    device_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> None:
    """Delete a Device by its device_id.

    Also deletes all associated journal entries.
    Prevents deletion if Device is referenced by any tests.
    """
    # Check if Device exists
    if not mongo.devices.find_one({"_id": device_id}):
        raise HTTPException(status_code=404, detail="Device not found")

    # Check if Device is referenced by any tests (only fetch _id field for performance)
    tests_referencing_device = list(mongo.tests.find({"devices.device_id": device_id}, {"_id": 1}))
    if tests_referencing_device:
        test_ids = [test["_id"] for test in tests_referencing_device]
        test_count = len(test_ids)
        test_list = ", ".join(test_ids[:5])  # Show up to 5 test IDs
        if test_count > 5:
            test_list += f" and {test_count - 5} more"
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete Device: Referenced by {test_count} test(s): {test_list}"
        )

    # Delete all journal entries for this Device
    mongo.device_journal.delete_many({"device_id": device_id})

    # Delete the Device
    mongo.devices.delete_one({"_id": device_id})


@router.get(
    "/devices/{device_id}/journal",
    response_model=list[DeviceJournalEntry],
    response_model_by_alias=False,
)
def get_device_journal(
    device_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[DeviceJournalEntry]:
    """Get all journal entries for a Device.

    Returns entries sorted by timestamp in descending order (newest first).
    """
    # Check if Device exists
    if not mongo.devices.find_one({"_id": device_id}):
        raise HTTPException(status_code=404, detail="Device not found")

    journal_entries = mongo.device_journal.find({"device_id": device_id}).sort(
        "timestamp", -1
    )
    return [DeviceJournalEntry(**entry) for entry in journal_entries]


@router.get(
    "/devices/{device_id}/journal/summary",
    response_model=list[DeviceJournalEntrySummary],
    response_model_by_alias=False,
)
def get_device_journal_summary(
    device_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[DeviceJournalEntrySummary]:
    """Get journal entries for a Device without full Device snapshots.

    This endpoint optimizes performance by excluding the `data` field,
    reducing payload size by 60-80% for journal list views.
    Returns entries sorted by timestamp in descending order (newest first).
    """
    # Check if Device exists
    if not mongo.devices.find_one({"_id": device_id}):
        raise HTTPException(status_code=404, detail="Device not found")

    journal_entries = mongo.device_journal.find(
        {"device_id": device_id},
        {"data": 0}  # Exclude data field
    ).sort("timestamp", -1)
    return [DeviceJournalEntrySummary(**entry) for entry in journal_entries]


@router.post(
    "/devices/{device_id}/journal",
    response_model=DeviceJournalEntry,
    response_model_by_alias=False,
)
def create_device_journal_entry(
    device_id: str,
    journal_data: DeviceJournalEntryCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> DeviceJournalEntry:
    """Create a manual journal entry for a Device.

    This allows users to add notes/observations without modifying the Device itself.
    The journal entry captures the current Device state as a snapshot.
    """
    # Check if Device exists and get current state
    if not (device := mongo.devices.find_one({"_id": device_id})):
        raise HTTPException(status_code=404, detail="Device not found")

    # Create journal entry with current Device snapshot
    journal_entry = create_journal_entry(
        mongo=mongo,
        device_id=device_id,
        editor=journal_data.editor,
        text=journal_data.text,
        data=device,  # Current Device state
        category=journal_data.category,
    )

    return journal_entry
