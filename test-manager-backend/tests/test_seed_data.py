"""Tests for automatic seed data functionality."""

from api.seed_data import (
    _extract_building_from_location,
    _generate_category_name,
    _generate_location_name,
    _generate_sample_type_name,
    parse_csv_for_lookups,
    seed_lookup_tables,
)


class TestHelperFunctions:
    """Test the helper functions for generating names."""

    def test_generate_sample_type_name(self):
        """Test sample type name generation."""
        assert _generate_sample_type_name("PFP") == "Pre-Final Prototype"
        assert _generate_sample_type_name("FP") == "Final Prototype"
        assert _generate_sample_type_name("A") == "Sample A"
        assert _generate_sample_type_name("Unknown") == "Sample Unknown"

    def test_generate_location_name(self):
        """Test location name generation."""
        # Generic implementation returns location_id directly
        assert _generate_location_name("Lab-A-01") == "Lab-A-01"
        assert _generate_location_name("TestBench-05") == "TestBench-05"
        assert _generate_location_name("Custom") == "Custom"

    def test_extract_building_from_location(self):
        """Test building extraction from location ID."""
        # Generic implementation extracts first two parts
        assert _extract_building_from_location("Lab-A-01") == "Lab A"
        assert _extract_building_from_location("TestBench-05") == "TestBench 05"
        assert _extract_building_from_location("Custom") is None

    def test_generate_category_name(self):
        """Test category name generation."""
        # Generic categories
        assert _generate_category_name("Electronics") == "Electronics"
        assert _generate_category_name("Mechanical") == "Mechanical Components"
        assert _generate_category_name("Software") == "Software Modules"
        assert _generate_category_name("Sensors") == "Sensor Systems"
        # Unknown category should use title case
        assert _generate_category_name("custom") == "Custom"


class TestCSVParsing:
    """Test CSV parsing functionality."""

    def test_parse_csv_for_lookups(self):
        """Test parsing the real CSV file."""
        lookup_data = parse_csv_for_lookups()

        # Should have data for lookup tables
        assert "sample_types" in lookup_data
        assert "locations" in lookup_data
        assert "product_categories" in lookup_data

        # Sample types should be extracted
        sample_types = lookup_data["sample_types"]
        assert len(sample_types) > 0
        # Check structure of first sample type
        if sample_types:
            st = sample_types[0]
            assert "_id" in st
            assert "sample_type" in st

        # Locations should be extracted
        locations = lookup_data["locations"]
        assert len(locations) > 0
        # Check structure of first location
        if locations:
            loc = locations[0]
            assert "_id" in loc
            assert "location" in loc

    def test_csv_extracts_expected_values(self):
        """Test that specific expected values are extracted."""
        lookup_data = parse_csv_for_lookups()

        # Check for known sample types from the CSV
        sample_type_ids = [st["_id"] for st in lookup_data["sample_types"]]
        assert "PFP" in sample_type_ids
        assert "FP" in sample_type_ids
        assert "A" in sample_type_ids

        # Check for known locations from the CSV
        location_ids = [loc["_id"] for loc in lookup_data["locations"]]
        # Should have generic test locations
        assert any(loc_id.startswith("Lab-") or loc_id.startswith("Bench-") for loc_id in location_ids)


class TestSeeding:
    """Test the seeding functionality."""

    def test_seed_lookup_tables_via_api(self, client):
        """Test that seeding happened when the app started."""
        # The seeding should have happened automatically on app startup
        # via the lifespan function in app.py

        # Verify data was seeded by checking the lookup endpoints
        response = client.get("/api/v1/lookups/sample-types")
        assert response.status_code == 200
        sample_types = response.json()
        assert len(sample_types) > 0

        response = client.get("/api/v1/lookups/locations")
        assert response.status_code == 200
        locations = response.json()
        assert len(locations) > 0

        # Verify structure of seeded data
        # Note: API returns with alias=False, so _id becomes id
        assert "id" in sample_types[0]
        assert "sample_type" in sample_types[0]

        assert "id" in locations[0]
        assert "location" in locations[0]
