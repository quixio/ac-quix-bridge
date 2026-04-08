from typing import Any

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from ..auth import read_permission
from ..mongo import get_mongo
from ..models import SampleType, Location, ProductCategory, Product

router = APIRouter()


@router.get(
    "/lookups/sample-types",
    response_model=list[SampleType],
    response_model_by_alias=False,
)
def list_sample_types(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[SampleType]:
    """Get all available sample types.

    Sample types are read-only lookup values (e.g., PFP, FP, A, B).
    These are maintained manually by an admin directly in MongoDB.
    """
    sample_types = mongo.sample_types.find()
    return [SampleType(**st) for st in sample_types]


@router.get(
    "/lookups/locations", response_model=list[Location], response_model_by_alias=False
)
def list_locations(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[Location]:
    """Get all available locations.

    Locations are read-only lookup values (e.g., Bench 3, Site A, Lab 2).
    These are maintained manually by an admin directly in MongoDB.
    """
    locations = mongo.locations.find()
    return [Location(**loc) for loc in locations]


@router.get(
    "/lookups/product-categories",
    response_model=list[ProductCategory],
    response_model_by_alias=False,
)
def list_product_categories(
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[ProductCategory]:
    """Get all available product categories.

    Product categories are read-only lookup values (e.g., WP, Gas, PV).
    These are maintained manually by an admin directly in MongoDB.
    """
    categories = mongo.product_categories.find()
    return [ProductCategory(**cat) for cat in categories]


@router.get(
    "/lookups/products",
    response_model=list[Product],
    response_model_by_alias=False,
)
def list_products(
    manufacturer: str | None = Query(None),
    product_category: str | None = Query(None),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[Product]:
    """Get all available products with optional filtering.

    Products are read-only lookup values.
    These are maintained manually by an admin directly in MongoDB.

    Args:
        manufacturer: Filter by manufacturer name
        product_category: Filter by product category
    """
    query_filter: dict[str, Any] = {}
    if manufacturer:
        query_filter["manufacturer"] = manufacturer
    if product_category:
        query_filter["product_category"] = product_category

    products = mongo.products.find(query_filter)
    return [Product(**prod) for prod in products]
