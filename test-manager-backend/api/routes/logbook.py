from uuid import uuid4
from typing import Any
from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..models import LogbookEntry, LogbookEntryCreate, LogbookEntryUpdate
from ..mongo import get_mongo

router = APIRouter()


@router.post("/tests/{test_id}/logbook", response_model=LogbookEntry, response_model_by_alias=False)
def create_logbook_entry(
    test_id: str,
    logbook_entry_data: LogbookEntryCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> LogbookEntry:
    if not mongo.tests.find_one({"_id": test_id}):
        raise HTTPException(status_code=404, detail="Test not found")

    entry = LogbookEntry(
        _id=str(uuid4()),
        test_id=test_id,
        **logbook_entry_data.model_dump(),
    )
    mongo.logbook.insert_one(entry.model_dump(by_alias=True))
    return entry


@router.get("/tests/{test_id}/logbook/{entry_id}", response_model=LogbookEntry, response_model_by_alias=False)
def get_logbook_entry(
    test_id: str,
    entry_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> LogbookEntry:
    if not (entry := mongo.logbook.find_one({"_id": entry_id, "test_id": test_id})):
        raise HTTPException(status_code=404, detail="Logbook entry not found")
    return LogbookEntry(**entry)


@router.get("/tests/{test_id}/logbook", response_model=list[LogbookEntry], response_model_by_alias=False)
def get_logbook_entries(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[LogbookEntry]:
    entries = mongo.logbook.find({"test_id": test_id}).sort("created_at", -1)
    return [LogbookEntry(**entry) for entry in entries]


@router.put("/tests/{test_id}/logbook/{entry_id}", response_model=LogbookEntry, response_model_by_alias=False)
def update_logbook_entry(
    test_id: str,
    entry_id: str,
    entry_update: LogbookEntryUpdate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> LogbookEntry:
    update_data = entry_update.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    if not (
        updated_entry := mongo.logbook.find_one_and_update(
            {"_id": entry_id, "test_id": test_id},
            {"$set": update_data},
            return_document=ReturnDocument.AFTER,
        )
    ):
        raise HTTPException(status_code=404, detail="Logbook entry not found")

    return LogbookEntry(**updated_entry)


@router.delete("/tests/{test_id}/logbook/{entry_id}", status_code=204)
def delete_logbook_entry(
    test_id: str,
    entry_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> None:
    result = mongo.logbook.delete_one({"_id": entry_id, "test_id": test_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Logbook entry not found")
