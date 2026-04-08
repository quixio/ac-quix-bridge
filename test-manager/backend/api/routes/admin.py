"""Admin endpoints for database management and demo data."""

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pymongo.database import Database

from ..auth import update_permission
from ..mongo import get_mongo
from ..models import DeviceStatus, TestStatus, JournalCategory

router = APIRouter()


def get_lookup_values(mongo: Database[dict[str, Any]]) -> dict[str, list[str]]:
    """Fetch actual lookup values from database."""
    sample_types = [doc["_id"] for doc in mongo.sample_types.find({}, {"_id": 1})]
    locations = [doc["_id"] for doc in mongo.locations.find({}, {"_id": 1})]

    # Fallback defaults if lookups are empty
    if not sample_types:
        sample_types = ["PFP", "FP", "A", "B", "C", "D", "S"]
    if not locations:
        locations = ["Insel-1.1", "Insel-2.3", "Insel-3.2", "Bench-A", "Lab-7"]

    return {
        "sample_types": sample_types,
        "locations": locations,
    }


def generate_demo_devices(
    sample_types: list[str], locations: list[str], num_devices: int = 10
) -> list[dict[str, Any]]:
    """Generate N realistic Device records by cycling through templates."""
    now = datetime.now(timezone.utc)

    # Define Device configuration templates to cycle through
    device_configs = [
        {
            "device_id": "Device-HP-001",
            "manufacturer": "Acme Corp",
            "product_category": "Heat Pump",
            "product_name": "Vitocal 250-A",
            "product_type": "AWO-AC 251.A10",
            "product_variant": "10kW",
            "sample_type": sample_types[0] if len(sample_types) > 0 else "PFP",
            "sample_nr": "001",
            "location": locations[0] if len(locations) > 0 else "Insel-1.1",
            "status": DeviceStatus.CREATED,
            "status_note": "Initial commissioning",
            "project": "Winter Efficiency 2025",
            "software_bundle": "v3.2.1",
        },
        {
            "device_id": "Device-HP-002",
            "manufacturer": "Acme Corp",
            "product_category": "Heat Pump",
            "product_name": "Vitocal 200-S",
            "product_type": "AWB-M 201.A08",
            "product_variant": "8kW",
            "sample_type": sample_types[1] if len(sample_types) > 1 else "FP",
            "sample_nr": "042",
            "location": locations[1] if len(locations) > 1 else "Insel-2.3",
            "status": DeviceStatus.SETUP,
            "status_note": "Sensors installed, calibration pending",
            "project": "Field Performance Study",
            "software_bundle": "v3.1.5",
        },
        {
            "device_id": "Device-HP-003",
            "manufacturer": "Bosch",
            "product_category": "Heat Pump",
            "product_name": "Compress 7800i AW",
            "product_type": "7TE",
            "product_variant": "13kW",
            "sample_type": sample_types[2] if len(sample_types) > 2 else "A",
            "sample_nr": "123",
            "location": locations[2] if len(locations) > 2 else "Insel-3.2",
            "status": DeviceStatus.STORED,
            "status_note": "Testing completed, awaiting next campaign",
            "project": "Comparative Analysis Q1",
            "software_bundle": "v2.8.3",
        },
        {
            "device_id": "Device-HP-004",
            "manufacturer": "Daikin",
            "product_category": "Heat Pump",
            "product_name": "Altherma 3 H HT",
            "product_type": "ETBH16D9W",
            "product_variant": "16kW",
            "sample_type": sample_types[3] if len(sample_types) > 3 else "B",
            "sample_nr": "007",
            "location": locations[3] if len(locations) > 3 else "Bench-A",
            "status": DeviceStatus.CREATED,
            "status_note": "Initial setup pending",
            "project": "High Temperature Testing",
            "software_bundle": "v4.0.1",
        },
        {
            "device_id": "Device-GB-001",
            "manufacturer": "Acme Corp",
            "product_category": "Gas Boiler",
            "product_name": "Vitodens 200-W",
            "product_type": "B2HB",
            "product_variant": "35kW",
            "sample_type": sample_types[0] if len(sample_types) > 0 else "PFP",
            "sample_nr": "002",
            "location": locations[4] if len(locations) > 4 else "Lab-7",
            "status": DeviceStatus.SETUP,
            "status_note": "Gas connection verified",
            "project": "Modulation Behavior Study",
            "software_bundle": "v5.1.2",
        },
        {
            "device_id": "Device-HP-005",
            "manufacturer": "Mitsubishi Electric",
            "product_category": "Heat Pump",
            "product_name": "Ecodan",
            "product_type": "PUHZ-SHW112YAA",
            "product_variant": "11kW",
            "sample_type": sample_types[1] if len(sample_types) > 1 else "FP",
            "sample_nr": "088",
            "location": locations[0] if len(locations) > 0 else "Insel-1.1",
            "status": DeviceStatus.CREATED,
            "status_note": "Arrived, inspection pending",
            "project": "Sound Level Assessment",
            "software_bundle": "v2.3.7",
        },
        {
            "device_id": "Device-HP-006",
            "manufacturer": "Vaillant",
            "product_category": "Heat Pump",
            "product_name": "aroTHERM plus",
            "product_type": "VWL 125/6 A",
            "product_variant": "12kW",
            "sample_type": sample_types[2] if len(sample_types) > 2 else "A",
            "sample_nr": "201",
            "location": locations[1] if len(locations) > 1 else "Insel-2.3",
            "status": DeviceStatus.STORED,
            "status_note": "Phase 1 testing complete",
            "project": "Energy Efficiency Study",
            "software_bundle": "v1.9.4",
        },
        {
            "device_id": "Device-GB-002",
            "manufacturer": "Bosch",
            "product_category": "Gas Boiler",
            "product_name": "Condens 9000i W",
            "product_type": "GC9000iW",
            "product_variant": "20kW",
            "sample_type": sample_types[4] if len(sample_types) > 4 else "C",
            "sample_nr": "015",
            "location": locations[2] if len(locations) > 2 else "Insel-3.2",
            "status": DeviceStatus.CREATED,
            "status_note": "Ready for commissioning",
            "project": "Condensing Efficiency Analysis",
            "software_bundle": "v3.4.0",
        },
        {
            "device_id": "Device-HP-007",
            "manufacturer": "LG",
            "product_category": "Heat Pump",
            "product_name": "Therma V",
            "product_type": "HM141M.U33",
            "product_variant": "14kW",
            "sample_type": sample_types[5] if len(sample_types) > 5 else "D",
            "sample_nr": "099",
            "location": locations[3] if len(locations) > 3 else "Bench-A",
            "status": DeviceStatus.SETUP,
            "status_note": "All sensors connected and verified",
            "project": "Smart Grid Integration",
            "software_bundle": "v4.2.1",
        },
        {
            "device_id": "Device-HP-008",
            "manufacturer": "Panasonic",
            "product_category": "Heat Pump",
            "product_name": "Aquarea",
            "product_type": "WH-MDC12H9E8",
            "product_variant": "12kW",
            "sample_type": sample_types[6] if len(sample_types) > 6 else "S",
            "sample_nr": "112",
            "location": locations[4] if len(locations) > 4 else "Lab-7",
            "status": DeviceStatus.SCRAPPED,
            "status_note": "Compressor failure during endurance test",
            "project": "Endurance Testing Program",
            "software_bundle": "v2.7.9",
        },
    ]

    # Build complete Device documents by cycling through templates
    devices = []
    for i in range(num_devices):
        # Cycle through config templates
        config = device_configs[i % len(device_configs)]

        # Generate unique ID
        device_num = i + 1
        if "HP" in config["device_id"]:
            device_id = f"Device-HP-{device_num:03d}"
        elif "GB" in config["device_id"]:
            device_id = f"Device-GB-{device_num:03d}"
        else:
            device_id = f"Device-{device_num:03d}"

        # sample_nr may be a string in config or we generate from device_num
        sample_nr = config.get('sample_nr', f"{device_num:03d}")
        sample_id = f"{config['sample_type']}-{sample_nr}"

        device = {
            "_id": device_id,
            "status": config["status"],
            "status_note": config["status_note"],
            "created_at": now - timedelta(days=min(90, 30 + i * 2)),
            "updated_at": now - timedelta(days=min(60, 20 + i)),
            "creator": "demo.admin",
            "last_editor": "demo.admin",
            # Product fields
            "manufacturer": config["manufacturer"],
            "product_category": config["product_category"],
            "product_name": config["product_name"],
            "product_type": config.get("product_type"),
            "product_variant": config.get("product_variant"),
            "product_key": None,
            # Sample fields
            "sample_type": config["sample_type"],
            "sample_nr": f"{device_num:03d}",
            "sample_id": sample_id,
            # Organization
            "sample_owner": "Test Team",
            "location": config["location"],
            "project": config.get("project"),
            "picture_link": f"https://pictures.example.com/{device_id}",
            # Metadata
            "software_bundle": config.get("software_bundle"),
            "hardware_link": f"https://hardware.example.com/{device_id}",
            "comment": f"Demo Device for {config.get('project', 'testing')}",
            "attended_operation": False,
            "unattended_operation": False,
        }
        devices.append(device)

    return devices


def generate_demo_tests(devices: list[dict[str, Any]], num_tests: int = 10) -> list[dict[str, Any]]:
    """Generate N realistic Test records referencing the created Devices."""
    now = datetime.now(timezone.utc)

    # Extract Device IDs for referencing
    device_ids = [device["_id"] for device in devices]

    # If no Devices, can't create tests
    if not device_ids:
        return []

    # Define test scenario templates to cycle through (Devices assigned randomly below)
    test_configs = [
        {
            "test_id": "TEST-2025-001",
            "campaign_id": "WINTER-EFF-2025",
            "status": TestStatus.DRAFT,
            "operator": "John Smith",
            "environment_id": "Environment-BENCH-01",
        },
        {
            "test_id": "TEST-2025-002",
            "campaign_id": "FIELD-PERF-Q1",
            "status": TestStatus.DRAFT,
            "operator": "Sarah Connor",
            "environment_id": "Environment-BENCH-02",
        },
        {
            "test_id": "TEST-2025-003",
            "campaign_id": "COMPARATIVE-Q1",
            "status": TestStatus.IN_PROGRESS,
            "operator": "Michael Chen",
            "environment_id": "Environment-BENCH-03",
            "start": now - timedelta(days=5),
        },
        {
            "test_id": "TEST-2025-004",
            "campaign_id": "COMPARATIVE-Q1",
            "status": TestStatus.IN_PROGRESS,
            "operator": "Emma Wilson",
            "environment_id": "Environment-BENCH-01",
            "start": now - timedelta(days=10),
        },
        {
            "test_id": "TEST-2025-005",
            "campaign_id": "ENDURANCE-2024",
            "status": TestStatus.FINISHED,
            "operator": "David Kim",
            "environment_id": "Environment-ENDURANCE-01",
            "start": now - timedelta(days=90),
            "end": now - timedelta(days=5),
        },
        {
            "test_id": "TEST-2025-006",
            "campaign_id": "ENERGY-EFF-STUDY",
            "status": TestStatus.FINISHED,
            "operator": "Lisa Anderson",
            "environment_id": "Environment-BENCH-02",
            "start": now - timedelta(days=30),
            "end": now - timedelta(days=3),
        },
        {
            "test_id": "TEST-2025-007",
            "campaign_id": "MODULATION-STUDY",
            "status": TestStatus.DRAFT,
            "operator": "Robert Brown",
            "environment_id": "Environment-GAS-01",
        },
        {
            "test_id": "TEST-2025-008",
            "campaign_id": "SOUND-ASSESSMENT",
            "status": TestStatus.IN_PROGRESS,
            "operator": "Jennifer Lee",
            "environment_id": "Environment-ACOUSTIC-01",
            "start": now - timedelta(days=2),
        },
        {
            "test_id": "TEST-2025-009",
            "campaign_id": "HIGH-TEMP-2025",
            "status": TestStatus.FINISHED,
            "operator": "Thomas Garcia",
            "environment_id": "Environment-BENCH-03",
            "start": now - timedelta(days=45),
            "end": now - timedelta(days=7),
        },
        {
            "test_id": "TEST-2025-010",
            "campaign_id": "SMART-GRID-INT",
            "status": TestStatus.DRAFT,
            "operator": "Maria Rodriguez",
            "environment_id": "Environment-BENCH-01",
        },
    ]

    # Build complete Test documents by cycling through templates
    tests = []
    for i in range(num_tests):
        # Cycle through config templates
        config = test_configs[i % len(test_configs)]
        test_num = i + 1
        test_id = f"TEST-2025-{test_num:03d}"

        # Randomly assign 1-3 Devices to this test
        num_devices_for_test = random.randint(1, min(3, len(device_ids)))
        assigned_device_ids = random.sample(device_ids, num_devices_for_test)

        # Build Device references
        device_refs = [{"device_id": device_id, "device_version": None} for device_id in assigned_device_ids]

        # For in_progress and finished tests, set dac_version
        if config["status"] in [TestStatus.IN_PROGRESS, TestStatus.FINISHED]:
            for device_ref in device_refs:
                device_ref["device_version"] = str(uuid4())

        # Generate sensors configuration
        sensors = {}
        for j, device_id in enumerate(assigned_device_ids):
            sensor_id = f"SENSOR-{device_id}-T{j+1}"
            sensors[sensor_id] = {
                "type": "temperature",
                "location": "inlet",
                "unit": "celsius",
                "device_id": device_id,
            }
            sensor_id = f"SENSOR-{device_id}-P{j+1}"
            sensors[sensor_id] = {
                "type": "pressure",
                "location": "outlet",
                "unit": "bar",
                "device_id": device_id,
            }

        test = {
            "_id": test_id,
            "campaign_id": config["campaign_id"],
            "devices": device_refs,
            "environment_id": config["environment_id"],
            "environment_version": str(uuid4()) if config["status"] in [TestStatus.IN_PROGRESS, TestStatus.FINISHED] else None,
            "operator": config["operator"],
            "created_at": now - timedelta(days=min(120, 40 + i * 3)),
            "updated_at": now - timedelta(days=min(90, 30 + i * 2)),
            "sensors": sensors,
            "config_id": str(uuid4()),  # Auto-generated UUID (mimics Config API behavior)
            "config_type": "TestConfig",
            "target_key": test_id,
            "config_version": 1,
            "links": [],
            "files": {},
            "status": config["status"],
            "start": config.get("start"),
            "end": config.get("end"),
        }
        tests.append(test)

    return tests


def create_journal_entries(
    mongo: Database[dict[str, Any]], devices: list[dict[str, Any]]
) -> int:
    """Create 3-4 realistic journal entries for each Device showing progression over time."""
    journal_entries = []

    for device in devices:
        base_time = device["created_at"]

        # Entry 1: Initial creation (Day 0)
        journal_entries.append({
            "_id": str(uuid4()),
            "device_id": device["_id"],
            "timestamp": base_time,
            "editor": device["creator"],
            "category": JournalCategory.SETUP.value,
            "text": f"Device received and registered. Manufacturer: {device['manufacturer']}, Product: {device['product_name']}",
            "data": device,
        })

        # Entry 2: Setup/Configuration (Day 1-2)
        setup_time = base_time + timedelta(days=1, hours=3)
        setup_text = "Initial hardware configuration and electrical connections completed. Unit ready for functional tests."

        journal_entries.append({
            "_id": str(uuid4()),
            "device_id": device["_id"],
            "timestamp": setup_time,
            "editor": device["creator"],
            "category": JournalCategory.SETUP.value,
            "text": setup_text,
            "data": device,
        })

        # Entry 3: Testing (Day 3-5)
        test_time = base_time + timedelta(days=4, hours=2)
        journal_entries.append({
            "_id": str(uuid4()),
            "device_id": device["_id"],
            "timestamp": test_time,
            "editor": device["creator"],
            "category": JournalCategory.TESTING.value,
            "text": "Functional testing completed. All systems operational. Temperature sensors calibrated and verified.",
            "data": device,
        })

        # Entry 4: Status/Maintenance update (Day 7-14) - varies by Device
        update_time = base_time + timedelta(days=10, hours=5)
        update_texts = [
            f"Device moved to {device['location']} for long-term testing campaign",
            f"Software bundle updated. Ready for test campaign initiation.",
            "Maintenance check completed. All parameters within normal range.",
            "Device prepared for unattended operation testing",
        ]
        # Use device_id hash to consistently pick same update text for same Device
        update_idx = hash(device["_id"]) % len(update_texts)

        journal_entries.append({
            "_id": str(uuid4()),
            "device_id": device["_id"],
            "timestamp": update_time,
            "editor": device["creator"],
            "category": JournalCategory.SETUP.value,  # Using SETUP as there's no MAINTENANCE category
            "text": update_texts[update_idx],
            "data": device,
        })

    if journal_entries:
        mongo.device_journal.insert_many(journal_entries)

    return len(journal_entries)


def create_logbook_entries(
    mongo: Database[dict[str, Any]], tests: list[dict[str, Any]]
) -> int:
    """Create 2-5 realistic logbook entries for each Test showing test progression."""
    logbook_entries = []

    # Realistic logbook content templates
    logbook_templates = [
        "Test started successfully. All systems initialized and sensors responding normally.",
        "Temperature readings stable at target values. Proceeding with measurement phase.",
        "Minor fluctuation detected in sensor {sensor}. Investigating.",
        "Pressure readings within expected range. System performance nominal.",
        "Test paused for calibration check. Will resume in 30 minutes.",
        "Resumed testing after calibration. All values verified and confirmed.",
        "Unusual noise detected during operation. Documenting for analysis.",
        "Data collection progressing smoothly. {percent}% complete.",
        "System response time measured: {time}ms. Within specifications.",
        "Energy consumption tracking shows {result} performance vs baseline.",
        "Control logic responding correctly to all test conditions.",
        "Thermal efficiency measurements completed. Results look promising.",
        "Final measurements completed. Preparing test summary report.",
        "Test completed successfully. All objectives met.",
        "Anomaly detected and resolved. Root cause: {cause}.",
    ]

    operators = ["John Smith", "Sarah Connor", "Michael Chen", "Emma Wilson", "David Kim", "Lisa Anderson"]

    for test in tests:
        # Create 2-5 logbook entries per test
        num_entries = random.randint(2, 5)
        base_time = test.get("start") or test["created_at"]

        for j in range(num_entries):
            # Space entries throughout test duration
            entry_time = base_time + timedelta(hours=j * 6 + random.randint(0, 3))

            # Select random template and fill in variables
            template = random.choice(logbook_templates)
            content = template.format(
                sensor=f"SENSOR-T{random.randint(1,5)}",
                percent=random.randint(25, 95),
                time=random.randint(50, 500),
                result="better than expected" if random.random() > 0.5 else "within expected range",
                cause="sensor calibration drift" if random.random() > 0.7 else "external temperature fluctuation"
            )

            logbook_entries.append({
                "_id": str(uuid4()),
                "test_id": test["_id"],
                "created_at": entry_time,
                "timestamp": entry_time,
                "operator": random.choice(operators),
                "content": content,
                "sensor_ids": [f"SENSOR-{i}" for i in random.sample(range(1, 10), random.randint(0, 3))],
            })

    if logbook_entries:
        mongo.logbook.insert_many(logbook_entries)

    return len(logbook_entries)


@router.post("/admin/seed-demo-data", response_model=dict)
def seed_demo_data(
    num_devices: int = 10,
    num_tests: int = 10,
    include_journals: bool = True,
    include_logbook: bool = True,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict:
    """Admin endpoint to seed demo data for Tests and Devices.

    Args:
        num_devices: Number of Devices to create (1-100, default: 10)
        num_tests: Number of Tests to create (1-100, default: 10)
        include_journals: Include Device journal entries (default: True)
        include_logbook: Include Test logbook entries (default: True)

    This endpoint:
    1. Creates N realistic Device records using actual lookup data
    2. Creates M realistic Test records referencing the Devices
    3. Creates journal entries for each Device (if enabled)
    4. Creates logbook entries for each Test (if enabled)

    Returns:
        Summary of created records
    """
    # Validate parameters
    if not 1 <= num_devices <= 100:
        return {"error": "num_devices must be between 1 and 100"}
    if not 1 <= num_tests <= 100:
        return {"error": "num_tests must be between 1 and 100"}

    # Fetch lookup values
    lookups = get_lookup_values(mongo)

    # Generate demo data
    devices = generate_demo_devices(lookups["sample_types"], lookups["locations"], num_devices)
    tests = generate_demo_tests(devices, num_tests)

    # Insert new data
    mongo.devices.insert_many(devices)
    mongo.tests.insert_many(tests)

    # Create journal entries for Devices
    journal_count = 0
    if include_journals:
        journal_count = create_journal_entries(mongo, devices)

    # Create logbook entries for Tests
    logbook_count = 0
    if include_logbook:
        logbook_count = create_logbook_entries(mongo, tests)

    return {
        "devices_created": len(devices),
        "tests_created": len(tests),
        "journal_entries_created": journal_count,
        "logbook_entries_created": logbook_count,
        "message": f"Demo data seeded successfully: {len(devices)} Devices, {len(tests)} Tests, {journal_count} journal entries, {logbook_count} logbook entries",
    }
