from uuid import uuid4
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.database import Database

from ..auth import update_permission, read_permission
from ..models import Link, LinkCreate
from ..mongo import get_mongo

router = APIRouter()


@router.post("/tests/{test_id}/links", response_model=Link)
def add_link(
    test_id: str,
    link_data: LinkCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Link:
    link = Link(id=str(uuid4()), **link_data.model_dump())

    result = mongo.tests.update_one(
        {"_id": test_id},
        {"$push": {"links": link.model_dump()}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Test not found")

    return link


@router.get("/tests/{test_id}/links", response_model=list[Link])
def list_links(
    test_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[Link]:
    if not (test := mongo.tests.find_one({"_id": test_id})):
        raise HTTPException(status_code=404, detail="Test not found")

    return [Link(**link) for link in test.get("links", [])]


@router.put("/tests/{test_id}/links/{link_id}", response_model=Link)
def update_link(
    test_id: str,
    link_id: str,
    link_data: LinkCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Link:
    """Update an existing link in a test."""
    result = mongo.tests.update_one(
        {"_id": test_id, "links.id": link_id},
        {
            "$set": {
                "links.$.label": link_data.label,
                "links.$.url": link_data.url,
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Test or link not found")

    return Link(id=link_id, **link_data.model_dump())


@router.delete("/tests/{test_id}/links/{link_id}", status_code=204)
def delete_link(
    test_id: str,
    link_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> None:
    result = mongo.tests.update_one(
        {"_id": test_id},
        {"$pull": {"links": {"id": link_id}}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Test not found")

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
