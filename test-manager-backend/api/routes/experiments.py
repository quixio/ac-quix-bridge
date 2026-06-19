import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ..auth import update_permission, read_permission
from ..mongo import get_mongo
from ..models import (
    Experiment,
    ExperimentCreate,
    ExperimentQuery,
    PaginatedResponse,
)

router = APIRouter()


def generate_experiment_id(mongo: Database[dict[str, Any]]) -> str:
    """Generate the next auto-incremented experiment ID (EXP-0001, EXP-0002, etc.).

    Lexicographic `_id` sort assumes 4-digit zero-padding, so it would mis-order
    past EXP-9999 — a limitation shared by all the prefixed-ID entities here.
    """
    last = mongo.experiments.find_one(
        {"_id": {"$regex": r"^EXP-\d+$"}},
        sort=[("_id", -1)],
    )
    if last:
        last_num = int(last["_id"].split("-")[1])
        return f"EXP-{last_num + 1:04d}"
    return "EXP-0001"


@router.post("/experiments", response_model=Experiment, response_model_by_alias=False)
def create_experiment(
    exp_data: ExperimentCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> Experiment:
    """Create an Experiment with an auto-generated ID.

    Name is case-sensitive (it is the verbatim lake partition value), so a
    409 only fires on an exact-string collision.
    """
    if mongo.experiments.find_one({"name": exp_data.name}):
        raise HTTPException(
            status_code=409, detail=f"Experiment '{exp_data.name}' already exists"
        )

    experiment = Experiment(_id=generate_experiment_id(mongo), name=exp_data.name)
    try:
        mongo.experiments.insert_one(experiment.model_dump(by_alias=True))
    except DuplicateKeyError:
        # The find_one above is a fast path; the unique index is the real guard
        # against a concurrent create racing past it.
        raise HTTPException(
            status_code=409, detail=f"Experiment '{exp_data.name}' already exists"
        )
    return experiment


@router.get(
    "/experiments",
    response_model=PaginatedResponse[Experiment],
    response_model_by_alias=False,
)
def list_experiments(
    query_params: ExperimentQuery = Depends(),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> PaginatedResponse[Experiment]:
    """List Experiments with pagination and filtering."""
    page = query_params.page
    page_size = query_params.page_size

    query: dict[str, Any] = {}

    if query_params.name:
        # Case-insensitive search for UX. Note: create/uniqueness are
        # case-SENSITIVE (the name is the verbatim lake partition), so a search
        # hit doesn't imply a create would collide — intentional.
        query["name"] = {"$regex": re.escape(query_params.name), "$options": "i"}

    if query_params.q:
        words = query_params.q.strip().split()
        if words:
            word_conditions = []
            for word in words:
                word_pattern = {"$regex": re.escape(word), "$options": "i"}
                word_conditions.append(
                    {"$or": [{"_id": word_pattern}, {"name": word_pattern}]}
                )
            query["$and"] = word_conditions

    total = mongo.experiments.count_documents(query)
    skip = (page - 1) * page_size
    experiments = [
        Experiment(**d)
        for d in mongo.experiments.find(query)
        .sort("_id", 1)
        .skip(skip)
        .limit(page_size)
    ]
    return PaginatedResponse.create(
        items=experiments, total=total, page=page, page_size=page_size
    )


@router.get(
    "/experiments/{experiment_id}",
    response_model=Experiment,
    response_model_by_alias=False,
)
def get_experiment(
    experiment_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Experiment:
    """Get a single Experiment by ID."""
    doc = mongo.experiments.find_one({"_id": experiment_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return Experiment(**doc)


@router.delete("/experiments/{experiment_id}")
def delete_experiment(
    experiment_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    """Delete an Experiment.

    Safe under the name-binding model: tests store the experiment name string
    directly, so a deleted entity leaves existing tests untouched — it only
    drops out of the create-form dropdown.
    """
    result = mongo.experiments.delete_one({"_id": experiment_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return {"message": f"Experiment {experiment_id} deleted"}
