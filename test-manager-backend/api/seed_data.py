"""Automatic seeding of lookup tables from CSV data."""

import csv
import logging
from pathlib import Path
from typing import Any

from pymongo.database import Database

logger = logging.getLogger(__name__)

# Path to seed data CSV file (optional - falls back to defaults if not found)
SEED_DATA_PATH = Path(__file__).parent.parent / "seed_data" / "lookup_data.CSV"


def parse_csv_for_lookups() -> dict[str, list[dict[str, Any]]]:
    """
    Parse the lookup_data.CSV file and extract lookup data.

    Returns a dictionary with keys:
    - sample_types: List of unique sample types
    - locations: List of unique locations
    - product_categories: List of unique product categories

    Each item is a dict with _id and other relevant fields.
    """
    if not SEED_DATA_PATH.exists():
        logger.warning(f"Seed data file not found: {SEED_DATA_PATH}")
        return {
            "sample_types": [],
            "locations": [],
            "product_categories": [],
        }

    sample_types = set()
    locations = set()
    product_categories = set()

    try:
        # Try UTF-8 first, fall back to latin-1 for German characters
        encoding = "utf-8"
        try:
            with open(SEED_DATA_PATH, "r", encoding=encoding) as f:
                f.read()
        except UnicodeDecodeError:
            encoding = "latin-1"

        with open(SEED_DATA_PATH, "r", encoding=encoding) as f:
            # CSV uses semicolon delimiter
            reader = csv.DictReader(f, delimiter=";")

            for row in reader:
                # Extract sample types
                if sample_type := row.get("sample.type", "").strip():
                    sample_types.add(sample_type)

                # Extract locations
                if location := row.get("location", "").strip():
                    locations.add(location)

                # Extract product categories
                if category := row.get("product.category", "").strip():
                    product_categories.add(category)

    except Exception as e:
        logger.error(f"Error parsing seed data CSV: {e}", exc_info=True)
        return {
            "sample_types": [],
            "locations": [],
            "product_categories": [],
        }

    # Convert to list of dicts with proper structure
    # Note: The models just have _id and a simple field with the same name
    return {
        "sample_types": [
            {
                "_id": st,
                "sample_type": _generate_sample_type_name(st),
            }
            for st in sorted(sample_types)
            if st
        ],
        "locations": [
            {
                "_id": loc,
                "location": _generate_location_name(loc),
            }
            for loc in sorted(locations)
            if loc
        ],
        "product_categories": [],  # Not implemented in lookup API yet
    }


def _generate_sample_type_name(sample_type_id: str) -> str:
    """Generate a readable name for a sample type."""
    # Map known abbreviations
    name_map = {
        "PFP": "Pre-Final Prototype",
        "FP": "Final Prototype",
        "A": "Sample A",
        "B": "Sample B",
        "C": "Sample C",
        "D": "Sample D",
        "S": "Sample S",
        "x": "Sample X",
    }
    return name_map.get(sample_type_id, f"Sample {sample_type_id}")


def _generate_location_name(location_id: str) -> str:
    """Generate a readable name for a location."""
    # Location IDs are used directly as display names
    return location_id


def _extract_building_from_location(location_id: str) -> str | None:
    """Extract building identifier from location ID."""
    # Extract major identifier from location ID (e.g., "Lab-A-01" -> "Lab A")
    parts = location_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"
    return None


def _generate_category_name(category_id: str) -> str:
    """Generate a readable name for a product category."""
    # Generic product categories - use title case
    name_map = {
        "Electronics": "Electronics",
        "Mechanical": "Mechanical Components",
        "Software": "Software Modules",
        "Sensors": "Sensor Systems",
    }
    return name_map.get(category_id, category_id.title())


def seed_lookup_tables(mongo: Database[dict[str, Any]]) -> None:
    """
    Seed all lookup tables from CSV data if they are empty.

    This function is idempotent - it only seeds empty collections
    and will not overwrite existing data.

    Args:
        mongo: MongoDB database connection
    """
    logger.info("Checking lookup tables for seeding...")

    lookup_data = parse_csv_for_lookups()

    # Seed sample types
    if mongo.sample_types.count_documents({}) == 0:
        if lookup_data["sample_types"]:
            mongo.sample_types.insert_many(lookup_data["sample_types"])
            logger.info(f"✅ Seeded {len(lookup_data['sample_types'])} sample types")
        else:
            logger.warning("No sample types found in seed data")
    else:
        logger.info(f"Sample types already exist ({mongo.sample_types.count_documents({})} records)")

    # Seed locations
    if mongo.locations.count_documents({}) == 0:
        if lookup_data["locations"]:
            mongo.locations.insert_many(lookup_data["locations"])
            logger.info(f"✅ Seeded {len(lookup_data['locations'])} locations")
        else:
            logger.warning("No locations found in seed data")
    else:
        logger.info(f"Locations already exist ({mongo.locations.count_documents({})} records)")

    logger.info("Lookup table seeding complete")
