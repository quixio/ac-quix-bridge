# Test Manager System

A comprehensive test execution and device management system designed for engineering teams across any industry. Built with modern web technologies and designed for scalability, real-time data processing, and ease of customization.

## Overview

Test Manager is an industry-agnostic platform for managing:

- **Devices Under Test**: Track devices through their lifecycle (creation, setup, testing, storage, disposal)
- **Test Execution**: Plan and execute test campaigns with multiple devices and environments
- **Test Environments**: Manage test configurations and environment setups
- **Real-time Data**: Stream and visualize sensor data during tests
- **Audit Trail**: Complete journal history of all device and test changes
- **File Management**: Upload and organize test-related documents and media
- **External Links**: Connect test results to external tools (Grafana, Jira, etc.)

## Key Features

### Device Management
- Track devices with versioning and complete history
- Customizable device attributes (manufacturer, product info, location, status)
- Journal entries for all changes with automatic snapshots
- QR code generation for physical device labeling
- Flexible sample identification system

### Test Execution
- Multi-device tests with version locking
- Test status workflow (draft → in progress → finished)
- Real-time sensor data integration via Kafka/InfluxDB
- Grafana dashboard embedding
- Campaign organization

### Extensibility
- Generic data model adaptable to any industry
- Configurable lookup tables (sample types, locations, categories)
- RESTful API for integrations
- CSV-based seed data for easy customization

## Quick Start

### Prerequisites

- **Docker Desktop** (required) - All services run in containers
  - Download: https://www.docker.com/products/docker-desktop/
  - Must be running for all local development

### Local Development with dev.sh

The `./scripts/dev.sh` script manages the entire local development environment:

```bash
# Start all services (frontend, backend, MongoDB, InfluxDB, Config API)
./scripts/dev.sh start

# Access the application
# Frontend: http://localhost:3000
# Backend API: http://localhost:8080
# API Docs: http://localhost:8080/
```

That's it! No configuration needed for local development.

**Common commands:**

```bash
./scripts/dev.sh stop       # Stop all services
./scripts/dev.sh restart    # Restart after code changes
./scripts/dev.sh rebuild    # Rebuild after dependency changes
./scripts/dev.sh status     # Check service status
./scripts/dev.sh logs       # View logs
```

For detailed local development instructions, see [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

## Architecture

```
┌─────────────────────────────────────────┐
│  Frontend (Next.js + TypeScript)       │
│  - React components                     │
│  - Tailwind CSS                         │
│  - Zod validation                       │
└─────────────┬───────────────────────────┘
              │
              │ REST API
              ▼
┌─────────────────────────────────────────┐
│  Backend (FastAPI + Python)             │
│  - Pydantic models                      │
│  - MongoDB for documents                │
│  - InfluxDB for time-series data        │
│  - Kafka for streaming                  │
└─────────────┬───────────────────────────┘
              │
              ├──► MongoDB (devices, tests, journals)
              ├──► InfluxDB (sensor data, metrics)
              └──► Kafka (event streaming)
```

### Technology Stack

**Frontend:**
- Next.js 15 (App Router)
- TypeScript
- React 19
- Tailwind CSS
- Radix UI components
- Zod schema validation
- TanStack React Query

**Backend:**
- FastAPI (Python)
- Pydantic v2
- MongoDB (primary database)
- InfluxDB (time-series data)
- Apache Kafka (event streaming)
- uv (package management)

**Infrastructure:**
- Docker & Docker Compose
- Quix Cloud (optional deployment)
- Azure Blob Storage (file uploads)

## Documentation

- **[Getting Started Guide](docs/GETTING_STARTED.md)** - Comprehensive setup and first-time configuration
- **[Local Development](docs/LOCAL_DEVELOPMENT.md)** - Docker-based development workflow with dev.sh
- **[Domain Model](docs/domain_model_requirements.md)** - Complete data model reference
- **[Accessibility](docs/ACCESSIBILITY.md)** - Accessibility features and WCAG compliance

## Testing

### Backend Tests

```bash
# Run all backend tests (using testcontainers)
cd backend && uv run pytest -v

# Run specific test file
uv run pytest tests/test_devices.py -v

# Run tests matching pattern
uv run pytest -k "create" -v
```

**133 tests** covering devices, tests, lookups, validation, and more.

### Frontend E2E Tests

```bash
# Run E2E tests in Docker
docker compose -f docker-compose.dev.yml exec frontend npm run test:e2e

# Run specific test file
docker compose -f docker-compose.dev.yml exec frontend npx playwright test token-refresh.spec.ts
```

## Deployment

The system can be deployed to:

1. **Quix Cloud** - Managed Kafka and real-time data processing (recommended)
2. **Any Docker environment** - Self-hosted with docker-compose
3. **Kubernetes** - Production-grade orchestration

For Quix Cloud deployment, see [CLAUDE.md](CLAUDE.md) for detailed instructions.

## Project Structure

```
.
├── backend/                # FastAPI backend
│   ├── api/               # API routes and models
│   ├── seed_data/         # CSV data for lookup tables
│   └── tests/             # Backend tests (pytest)
├── frontend/              # Next.js frontend
│   ├── app/              # Next.js app router pages
│   ├── components/       # React components
│   ├── lib/              # Utilities, hooks, schemas
│   └── types/            # TypeScript type definitions
├── docs/                  # Documentation
├── scripts/              # Development scripts (including dev.sh)
├── migrations/           # Database migration scripts
├── docker-compose.yml    # Production compose file
└── quix.yaml            # Quix Cloud configuration
```
