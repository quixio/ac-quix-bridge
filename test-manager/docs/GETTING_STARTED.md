# Getting Started with Test Manager

This guide will walk you through setting up Test Manager for the first time, from installation to running your first test.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [First-Time Setup](#first-time-setup)
4. [Core Concepts](#core-concepts)
5. [Your First Device](#your-first-device)
6. [Your First Test](#your-first-test)
7. [Next Steps](#next-steps)

## Prerequisites

### Required Software

1. **Docker Desktop**
   - Download: https://www.docker.com/products/docker-desktop/
   - Must be running for all local development
   - Verify installation: `docker ps` should work without errors

2. **Git** (for cloning the repository)
   - Download: https://git-scm.com/downloads

### Optional (for development)

3. **Python 3.13+** (for backend tests)
   - Verify: `python3 --version`
   - Install uv: https://docs.astral.sh/uv/

4. **Node.js 18+** (for frontend development outside Docker)
   - Download: https://nodejs.org/

## Installation

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd test-manager
```

### 2. Start All Services

```bash
# Start everything with one command
./scripts/dev.sh start
```

This will:
- Start the frontend (Next.js) on port 3000
- Start the backend (FastAPI) on port 8080
- Start MongoDB on port 27017
- Start InfluxDB on port 8086
- Start a mock config API on port 8001
- Automatically seed lookup tables with initial data

### 3. Access the Application

Open your browser and navigate to:
- **Frontend**: http://localhost:3000
- **Backend API Docs**: http://localhost:8080/

You should see the Test Manager homepage with a "Local Dev" badge in the header.

## First-Time Setup

### Verify Services are Running

```bash
# Check service status
./scripts/dev.sh status

# View logs if needed
./scripts/dev.sh logs backend
./scripts/dev.sh logs frontend
```

All services should show as "healthy" (except frontend which doesn't have a health check).

### Customize Seed Data (Optional)

Before creating devices, you may want to customize the lookup data for your industry:

1. Edit `backend/seed_data/lookup_data.CSV`
2. Modify sample types, locations, and categories
3. Restart services: `./scripts/dev.sh restart`

## Core Concepts

### Device

A **Device Under Test** represents a physical unit being tested. Key attributes:

- **device_id**: Unique identifier (e.g., "PROTO-001")
- **manufacturer**: Who made it
- **product_name**: What it is
- **sample_type**: Type of sample (PFP, FP, A, B, C, etc.)
- **location**: Where it's physically located
- **status**: Current state (created, setup, testing, stored, etc.)
- **version**: Automatically incremented on each update

### Test

A **Test** represents a test execution campaign involving one or more devices:

- **test_id**: Unique identifier (e.g., "TEST-2025-001")
- **devices**: List of devices with locked versions
- **environment_id**: Test environment configuration
- **status**: Draft → In Progress → Finished
- **sensors**: Kafka topic mappings for real-time data

### Environment

A **Test Environment** defines the configuration and setup for tests:

- **environment_id**: Unique identifier
- **description**: What this environment is for
- **configuration**: Key-value pairs of settings
- **version**: Automatically incremented on changes

### Journal Entries

Every change to a device or test environment creates a **journal entry** with:
- Timestamp
- User who made the change
- What changed (field-by-field diff)
- Full snapshot of the object
- Optional manual notes

## Your First Device

### 1. Navigate to Devices

Click "Devices" in the sidebar or navigate to http://localhost:3000/devices

### 2. Create a New Device

Click the **"Add Device"** button and fill in the form:

```
Device ID:        DEV-001
Creator:          your-username
Manufacturer:     Acme Corp
Product Category: Electronics
Product Name:     Widget Prototype
Sample Type:      PFP (Pre-Final Prototype)
Location:         Lab-A-01
Status:           Created
```

### 3. View the Device

After creating, you'll see:
- **Device Details**: All attributes and current status
- **Journal**: Automatic entry showing device creation
- **QR Code**: Scannable code for physical labeling
- **Version**: v1 (increments on each update)

### 4. Update the Device

Click **"Edit"** and change the status to "Setup". After saving:
- Version increments to v2
- New journal entry created automatically
- Full history preserved

### 5. Add a Manual Journal Entry

Click **"Add Journal Entry"** to add manual notes:

```
Text: Installed temperature and humidity sensors
```

This adds context to the automatic change tracking.

## Your First Test

### 1. Navigate to Tests

Click "Tests" in the sidebar or navigate to http://localhost:3000/tests

### 2. Create a New Test

Click **"Add Test"** and fill in the form:

```
Test ID:          TEST-001
Campaign ID:      WINTER-2025
Devices:          [Select DEV-001 from dropdown]
Environment ID:   ENV-001
Operator:         your-username
Status:           draft
```

### 3. Configure Sensors (Optional)

If you have Kafka streams:

```json
{
  "temperature": {
    "topic": "temperature-readings",
    "param_id": "temp_celsius"
  },
  "humidity": {
    "topic": "humidity-readings",
    "param_id": "humidity_percent"
  }
}
```

### 4. Start the Test

1. Click **"Edit"**
2. Change status to **"in_progress"**
3. The system locks device versions automatically
4. Sensor data streams begin (if configured)

### 5. View Test Data

- **Logbook**: Add timestamped observations during the test
- **Files**: Upload test reports, photos, logs
- **Links**: Add Grafana dashboard links, Jira tickets, etc.

### 6. Finish the Test

Change status to **"finished"** when complete. The test record is now read-only.

## Understanding the UI

### Navigation

- **Home**: Dashboard with stats and recent activity
- **Devices**: Browse, search, and manage devices
- **Tests**: Create and manage test executions
- **Dark Mode**: Toggle in the header (user preference)

### Search and Filtering

Both Devices and Tests pages support:
- **Text Search**: Searches across multiple fields
- **Filters**: Status, manufacturer, location, etc.
- **Pagination**: Configurable page size

### Status Badges

Visual indicators show the state of devices and tests:
- **Devices**: Created (gray), Setup (blue), Testing (yellow), etc.
- **Tests**: Draft (gray), In Progress (blue), Finished (green)

## Common Workflows

### Complete Device Lifecycle

1. **Create** device with status "created"
2. **Setup** - Install sensors, configure environment
3. **Testing** - Run tests, log observations
4. **Stored** - Between test campaigns
5. **Disposed** - End of life

Each status change creates a journal entry.

### Test Campaign Workflow

1. **Plan**: Create test in "draft" status
2. **Prepare**: Select devices, configure environment
3. **Execute**: Change to "in_progress", collect data
4. **Document**: Upload files, add logbook entries
5. **Complete**: Change to "finished"

### Adding External Context

For any test:
- **Upload Files**: PDFs, images, CSV data files
- **Add Links**: Grafana dashboards, Jira tickets, confluence pages
- **Logbook**: Timestamped observations and notes

## Troubleshooting

### Services Won't Start

```bash
# Stop everything
./scripts/dev.sh stop

# Clean volumes (WARNING: deletes data)
./scripts/dev.sh clean

# Start fresh
./scripts/dev.sh start
```

### Frontend Shows "Connection Error"

1. Check backend is running: `./scripts/dev.sh status`
2. Check backend logs: `./scripts/dev.sh logs backend`
3. Verify backend URL: Should be `http://backend:8080` (internal Docker networking)

### Database is Empty

The system auto-seeds lookup tables on first start. If empty:

1. Check logs: `./scripts/dev.sh logs backend`
2. Look for: `"✅ Seeded X sample types"` and `"✅ Seeded X locations"`
3. If missing, ensure `backend/seed_data/lookup_data.CSV` exists

### Port Already in Use

If ports 3000, 8080, 27017, etc. are taken:

1. Stop conflicting services
2. Or modify ports in `docker-compose.yml`
3. Restart: `./scripts/dev.sh restart`

## Next Steps

### Learn More

- **[Domain Model](domain_model_requirements.md)** - Understand all entities and fields
- **[Local Development](LOCAL_DEVELOPMENT.md)** - Complete development workflow guide

### Development

- **[Local Development](LOCAL_DEVELOPMENT.md)** - Docker workflow, hot reload, testing
- **[Frontend README](../frontend/README.md)** - React components, TypeScript, schemas

### Advanced Features

- **Real-time Sensor Data**: Configure Kafka topics and InfluxDB queries
- **Grafana Integration**: Embed dashboards in test view
- **API Integrations**: Use the FastAPI backend `/docs` for API reference
- **Authentication**: Configure Quix Cloud auth for production deployment

## Getting Help

- **Documentation**: Review all docs in `docs/` directory
- **API Documentation**: http://localhost:8080/ (interactive Swagger UI)
- **Issues**: Check GitHub issues or create a new one
- **Logs**: Always check logs first: `./scripts/dev.sh logs`

## Quick Reference

### Essential Commands

```bash
# Start services
./scripts/dev.sh start

# Stop services
./scripts/dev.sh stop

# Restart after code changes
./scripts/dev.sh restart

# Rebuild after dependency changes
./scripts/dev.sh rebuild

# View logs
./scripts/dev.sh logs [service]

# Check status
./scripts/dev.sh status
```

### Key URLs

- Frontend: http://localhost:3000
- Backend API: http://localhost:8080
- API Docs: http://localhost:8080/
- MongoDB: mongodb://test-manager:local-dev-password@localhost:27017/test_manager

### Default Credentials

**Local Development**: No authentication required (LOCAL_DEV_MODE=true)

**Production**: Configure Quix Cloud authentication

---

Welcome to Test Manager! Start by creating your first device and test to explore the system.
