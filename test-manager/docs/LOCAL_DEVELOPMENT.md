# Local Development Guide

Complete local development environment for the Test Manager - everything runs in Docker.

## Philosophy

**Fully local, zero cloud dependencies:**
- ✅ All services run locally in Docker (Frontend, Backend, MongoDB, InfluxDB, Config API)
- ✅ Mock authentication - no tokens or cloud services needed
- ✅ Unified dev script for all operations
- ✅ Hot reload for both frontend and backend
- ✅ Isolated, reproducible environments
- ❌ No manual infrastructure setup
- ❌ No internet required (except for Docker image pulls)

## Prerequisites

### Required Software

1. **Docker Desktop** (required)
   - Download: https://www.docker.com/products/docker-desktop/
   - **Must be running** for all local development
   - Verify: `docker ps` should work without errors

2. **Python 3.13+** (for backend tests only)
   - Verify: `python3 --version`
   - Install uv: https://docs.astral.sh/uv/

## Quick Start

**Start everything:**
```bash
./scripts/dev.sh start
```

**Access the application:**
- Frontend: http://localhost:3000
- Backend API: http://localhost:8080
- API Docs: http://localhost:8080/

**Stop everything:**
```bash
./scripts/dev.sh stop
```

That's it! No tokens, no configuration, no environment variables needed.

## Development Script

The `./scripts/dev.sh` script manages the entire local development environment.

### Common Commands

```bash
# Start all services
./scripts/dev.sh start

# Stop all services
./scripts/dev.sh stop

# Restart all services (after code changes)
./scripts/dev.sh restart

# Rebuild and restart (after dependency changes)
./scripts/dev.sh rebuild

# View status of all services
./scripts/dev.sh status

# View logs for all services
./scripts/dev.sh logs

# Follow logs (live tail)
./scripts/dev.sh logs-f

# View logs for specific service
./scripts/dev.sh logs backend
./scripts/dev.sh logs-f frontend

# Open shell in container
./scripts/dev.sh shell backend
./scripts/dev.sh shell frontend

# Clean everything (removes volumes - WARNING: deletes data!)
./scripts/dev.sh clean
```

### First Time Setup

No setup needed! Just run `./scripts/dev.sh start` and everything will be configured automatically.

## Services

The local environment runs 5 Docker containers:

| Service | Description | Port | Health Check |
|---------|-------------|------|--------------|
| **frontend** | Next.js UI | 3000 | N/A (no health check) |
| **backend** | FastAPI backend | 8080 | http://localhost:8080/health |
| **mongodb** | MongoDB database | 27017 | mongosh ping |
| **influxdb** | InfluxDB time-series | 8086 | curl /ping |
| **config-api** | Mock Config API | 8001 | http://localhost:8001/health |

All services are defined in `docker-compose.yml`.

## Development Workflow

### 🔥 Hot Reload - Code Changes Auto-Reload!

**Both frontend and backend have hot reload enabled - NO restart needed for code changes!**

When you save a file, changes are automatically detected and reloaded in 2-5 seconds.

**✅ Auto-reload (NO restart):**
- ✅ **Frontend**: TypeScript/React/CSS files → Next.js HMR
- ✅ **Backend**: Python code (models, routes, logic) → Uvicorn WatchFiles
- ✅ You'll see `Reloading...` in logs when changes are detected

**❌ Requires rebuild:**
- Adding/removing packages (`package.json`, `pyproject.toml`)
- Changes to `Dockerfile` or `docker-compose.yml`

### Frontend Development

**Make changes to React/TypeScript code:**
1. Edit files in `frontend/`
2. **Save** → Next.js automatically reloads (2-5 seconds)
3. Browser updates automatically at http://localhost:3000
4. **No restart needed!**

**After dependency changes:**
```bash
# Install new npm packages
docker compose exec frontend npm install <package>

# Rebuild container
./scripts/dev.sh rebuild
```

### Backend Development

**Make changes to Python code:**
1. Edit files in `backend/`
2. **Save** → Backend automatically reloads (2-5 seconds)
3. Check logs: `docker logs testmanager-backend --tail 10`
4. You'll see: `WatchFiles detected changes in 'file.py'. Reloading...`
5. **No restart needed!**

**After dependency changes:**
```bash
# The backend uses uv and mounts ./backend as a volume
# So changes to pyproject.toml require a rebuild

./scripts/dev.sh rebuild
```

### Database Access

**MongoDB:**
```bash
# Connect with mongo shell
docker compose exec mongodb mongosh -u test-manager -p local-dev-password

# Or use MongoDB Compass
# Connection string: mongodb://test-manager:local-dev-password@localhost:27017/test_manager
```

**InfluxDB:**
```bash
# Access InfluxDB CLI
docker compose exec influxdb influx -username admin -password local-dev-password

# Or use web UI
# http://localhost:8086 (username: admin, password: local-dev-password)
```

## Backend Testing

Tests run in isolated testcontainers (separate from the local dev environment).

### Quick Start

```bash
cd backend
uv run pytest -v
```

### What Happens

- Tests use **testcontainers** (automatic Docker containers)
- Spins up isolated: MongoDB, InfluxDB, Kafka
- Uses the **shared Mock Config API** from `mock_config_api/main.py`
- Runs all 79+ backend tests
- Containers automatically cleaned up after tests

### Test Commands

```bash
# All tests
uv run pytest -v

# Specific test file
uv run pytest tests/test_dacs.py -v

# Specific test
uv run pytest tests/test_dacs.py::test_create_dac -v

# Tests matching pattern
uv run pytest -k "create" -v

# Stop on first failure
uv run pytest -x

# Show print statements
uv run pytest -s
```

### Requirements

- **Docker Desktop must be running**
- Tests will fail with `DockerException` if Docker is unavailable
- No manual setup needed - testcontainers handles everything

## How It Works

### Local Development Mode

The backend automatically detects it's running in local mode via the `LOCAL_DEV_MODE=true` environment variable.

**What changes in local mode:**
- **Authentication**: Uses `LocalAuth` (always grants permission) instead of Quix Portal Auth
- **Config API**: Points to local mock service (`http://config-api:8001`)
- **Databases**: Uses local MongoDB and InfluxDB containers
- **No internet required**: Everything runs locally

**Implementation:**
- `backend/api/auth.py` - Conditionally imports `LocalAuth` or `Auth` based on environment
- `backend/api/local_auth.py` - Mock authentication implementation
- `backend/api/settings.py` - Default values for local development

### Mock Config API

The Mock Config API (`mock_config_api/`) is a standalone FastAPI service that mimics the Quix Dynamic Configuration Manager API.

**Features:**
- In-memory storage (resets on restart)
- Matches real API contract
- Shared between local dev and tests
- No persistence needed

**Endpoints:**
- `GET /health` - Health check
- `GET /api/v1/configurations` - List configurations
- `POST /api/v1/configurations` - Create configuration
- `GET /api/v1/configurations/{id}` - Get configuration
- `GET /api/v1/configurations/{id}/content` - Get content
- `PUT /api/v1/configurations/{id}` - Update configuration
- `DELETE /api/v1/configurations/{id}` - Delete configuration

## Architecture

### Local Development (All Local)

```
┌────────────────────────────────────────┐
│  Your Machine (Docker)                 │
│                                        │
│  ┌──────────────┐                     │
│  │  Frontend    │  http://localhost:3000
│  │  (Next.js)   │                     │
│  └──────┬───────┘                     │
│         │                              │
│         │ http://backend:8080          │
│         ▼                              │
│  ┌──────────────┐                     │
│  │  Backend API │  http://localhost:8080
│  │  (FastAPI)   │                     │
│  └──┬─────┬─────┘                     │
│     │     │                            │
│     │     └────────────┐              │
│     │                  │              │
│     ▼                  ▼              │
│  ┌─────────┐     ┌──────────┐        │
│  │ MongoDB │     │ InfluxDB │        │
│  └─────────┘     └──────────┘        │
│                                        │
│  ┌──────────────┐                     │
│  │  Config API  │  (Mock)             │
│  │  (Mock)      │                     │
│  └──────────────┘                     │
│                                        │
└────────────────────────────────────────┘
```

### Backend Testing (Isolated)

```
┌────────────────────────────────────────┐
│  Your Machine                          │
│                                        │
│  ┌──────────────────────┐             │
│  │  pytest              │             │
│  │  (test runner)       │             │
│  └──────────┬───────────┘             │
│             │                          │
│             │ Testcontainers           │
│             ▼                          │
│  ┌──────────────────────┐             │
│  │  Docker Containers   │             │
│  │  • MongoDB           │             │
│  │  • InfluxDB          │             │
│  │  • Kafka             │             │
│  │  • Mock Config API   │             │
│  └──────────────────────┘             │
│                                        │
│  (Isolated, auto-cleanup)              │
└────────────────────────────────────────┘
```

## Common Tasks

### View Logs

```bash
# All services
./scripts/dev.sh logs

# Specific service
./scripts/dev.sh logs backend
./scripts/dev.sh logs frontend

# Follow logs (live)
./scripts/dev.sh logs-f backend
```

### Restart After Changes

```bash
# Quick restart (code changes only)
./scripts/dev.sh restart

# Full rebuild (dependency changes)
./scripts/dev.sh rebuild
```

### Database Operations

```bash
# MongoDB shell
docker compose exec mongodb mongosh -u test-manager -p local-dev-password test_manager

# List collections
docker compose exec mongodb mongosh -u test-manager -p local-dev-password test_manager --eval "db.getCollectionNames()"

# Clear database
docker compose exec mongodb mongosh -u test-manager -p local-dev-password test_manager --eval "db.dropDatabase()"
```

### Clean Start

```bash
# WARNING: This deletes all data!
./scripts/dev.sh clean

# Then start fresh
./scripts/dev.sh start
```

## Troubleshooting

### Docker Issues

#### "Docker is not running"

**Symptoms:** `./scripts/dev.sh start` fails with Docker connection error

**Solutions:**
1. Start Docker Desktop
2. Verify Docker is running: `docker ps`
3. On WSL: Ensure Docker Desktop has WSL integration enabled

#### "Port already in use"

**Symptoms:** Container fails to start with port binding error

**Solutions:**
1. Check what's using the port: `lsof -i :3000` (or :8080, :27017, etc.)
2. Stop the conflicting service
3. Or use `./scripts/dev.sh clean` and restart

### Service Health Issues

#### Backend shows as "unhealthy"

**Solutions:**
1. Check logs: `./scripts/dev.sh logs backend`
2. Look for errors in startup
3. Verify MongoDB and InfluxDB are healthy
4. Check health endpoint: `curl http://localhost:8080/health`

#### MongoDB or InfluxDB not starting

**Solutions:**
1. Check logs: `./scripts/dev.sh logs mongodb`
2. Ensure no other MongoDB/InfluxDB is running on same ports
3. Try clean restart: `./scripts/dev.sh clean && ./scripts/dev.sh start`

### Frontend Issues

#### "Cannot connect to backend"

**Symptoms:** Frontend shows API connection errors

**Solutions:**
1. Verify backend is healthy: `./scripts/dev.sh status`
2. Check backend logs: `./scripts/dev.sh logs backend`
3. Test backend directly: `curl http://localhost:8080/health`
4. Restart services: `./scripts/dev.sh restart`

#### "ModuleNotFoundError" or npm errors

**Solutions:**
```bash
# Install dependencies
docker compose exec frontend npm install

# Or rebuild
./scripts/dev.sh rebuild
```

#### TypeScript errors not caught in dev mode

**Important:** Development mode (`npm run dev`) skips strict type checking for speed.

**Run type check manually:**
```bash
# Inside container
docker compose exec frontend npx tsc --noEmit

# Or full production build test
cd frontend && docker build -f dockerfile -t frontend-test .
```

**When to run:**
- Before committing changes
- Before creating a pull request
- After TypeScript changes
- If production deploy fails with type errors

### Backend Test Issues

#### "DockerException"

**Symptoms:** All tests fail with Docker errors

**Solutions:**
1. Start Docker Desktop
2. Verify: `docker ps`
3. On WSL: Enable WSL integration in Docker Desktop settings

#### Tests are slow

**Why:** Testcontainers pulls images and starts containers

**Tips:**
- First run is slow (downloads images)
- Subsequent runs faster (cached images)
- Use `-k` to run specific tests
- Use `-x` to stop on first failure

## Environment Variables

### Local Development (docker-compose.yml)

The following are automatically set:

```bash
# Backend
LOCAL_DEV_MODE=true              # Enables local development mode
API_AUTH_ACTIVE=false            # Disables API authentication
MONGO_USER=test-manager
MONGO_PASSWORD=local-dev-password
MONGO_HOST=mongodb
MONGO_PORT=27017
MONGO_DATABASE=test_manager
INFLUXDB_USER=admin
INFLUXDB_PASSWORD=local-dev-password
INFLUXDB_HOST=influxdb
INFLUXDB_PORT=8086
INFLUXDB_DATABASE=test_manager
CONFIG_API_URL=http://config-api:8001
Quix__Workspace__Id=local-dev-workspace
Quix__Sdk__Token=local-dev-token

# Frontend
NODE_ENV=development
API_URL=http://backend:8080      # Docker internal networking
NEXT_TELEMETRY_DISABLED=1
```

No manual configuration needed!

### Backend Tests

Tests use testcontainers which automatically configure their own environment variables.

## Deployment to Quix Cloud

Local development does NOT automatically deploy to cloud.

### Deploy Changes

1. **Commit changes:**
   ```bash
   git add .
   git commit -m "Your changes"
   git push origin <branch>
   ```

2. **Sync Quix environment:**
   ```bash
   quix cloud environments sync <workspace-id>
   ```

3. **Monitor deployment:**
   ```bash
   quix cloud deployments logs <deployment-id>
   ```

See [CLAUDE.md](../CLAUDE.md) for detailed Quix Cloud deployment workflow.

## Additional Resources

- **Quix CLI Docs**: https://quix.io/docs/quix-cli/cli-commands-summary.html
- **FastAPI Docs**: https://fastapi.tiangolo.com/
- **Next.js Docs**: https://nextjs.org/docs
- **uv Package Manager**: https://docs.astral.sh/uv/
- **Testcontainers**: https://testcontainers.com/
- **Docker Compose**: https://docs.docker.com/compose/

## Summary

| Task | Command | Duration | Requirements |
|------|---------|----------|--------------|
| **Full Local Dev** | `./scripts/dev.sh start` | ~30s first, ~5s after | Docker |
| **Backend Tests** | `cd backend && uv run pytest` | ~3 min | Docker, Python |
| **View Status** | `./scripts/dev.sh status` | Instant | Docker |
| **View Logs** | `./scripts/dev.sh logs` | Instant | Docker |

**Simple. Fast. Fully local. No cloud dependencies.**
