# Claude Code Project Context - Test Manager System

## Project Overview

This is the **Test Manager System** - a test execution and device management system built with:
- **Backend**: FastAPI + Python + MongoDB
- **Frontend**: Next.js (TypeScript + React)
- **Database**: MongoDB with collections for tests, devices, lookups, etc.

## Source of Truth

**ALL implementation decisions must follow:** [`docs/domain_model_requirements.md`](./docs/domain_model_requirements.md)

This document defines:
- All entities and their fields
- Relationships between entities
- Business rules and validation logic
- Data types and constraints
- Workflows and scenarios

**When in doubt, always refer to the domain model document.**

## Current Phase

We are implementing **Phase 2** following the plan in [`docs/phase2_implementation_plan.md`](./docs/phase2_implementation_plan.md).

Phase 2 introduces:
- **Device** (Device Under Test) as first-class entity
- **Multiple Devices per Test** with versioning
- **Journal entries** for audit trail
- **Safety Requirements** tracking
- **Product hierarchy** and lookup tables
- **Environment** (Test Environment) management

## Project Structure

```
.
├── backend/
│   ├── api/
│   │   ├── models.py           # Pydantic models for all entities
│   │   ├── routes/             # API endpoints organized by entity
│   │   │   ├── tests.py        # Test CRUD operations
│   │   │   ├── devices.py      # Device CRUD operations (Phase 2)
│   │   │   ├── files.py        # File upload/download
│   │   │   ├── logbook.py      # Logbook entries
│   │   │   └── links.py        # External links
│   │   ├── app.py              # FastAPI app setup
│   │   ├── auth.py             # Authentication/authorization
│   │   ├── mongo.py            # MongoDB connection
│   │   └── settings.py         # Configuration
│   ├── tests/                  # Backend unit tests
│   └── main.py                 # Entry point
├── frontend/
│   ├── app/
│   │   ├── models.py           # Frontend dataclasses (mirror backend)
│   │   ├── views.py            # UI views/pages
│   │   ├── components/         # Reusable UI components
│   │   ├── store.py            # State management
│   │   └── auth.py             # Frontend authentication
│   └── main.py                 # Entry point
├── migrations/                 # Database migrations
│   ├── README.md               # Migration overview
│   └── phase1_to_phase2/       # Phase 1→2 migration
│       ├── run_migration.sh    # Migration runner script
│       ├── run_test.sh         # Test data creation script
│       ├── phase1_to_phase2.py # Main migration logic
│       ├── test_migration.py   # Migration testing
│       ├── QUICK_START.md      # Quick reference
│       ├── README.md           # Complete guide
│       └── TEST_RESULTS.md     # Test verification
└── docs/                       # Documentation
    ├── domain_model_requirements.md  # SOURCE OF TRUTH
    └── phase2_implementation_plan.md
```

## Coding Patterns & Conventions

### Backend Patterns

#### 1. Models (Pydantic)

**Location**: `backend/api/models.py`

**Pattern to follow** (see existing `Test` model):
```python
class EntityStatus(str, Enum):
    """Entity status enum"""
    VALUE_1 = "value_1"
    VALUE_2 = "value_2"

class Entity(BaseModel):
    """Main entity model."""
    entity_id: str = Field(..., alias="_id")
    # ... other fields from domain model
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

class EntityCreate(BaseModel):
    """Create request model."""
    entity_id: str
    # ... required fields only

class EntityUpdate(BaseModel):
    """Update request model."""
    field1: str | None = None
    # ... all fields optional

class EntityQuery(BaseModel):
    """Query parameters for filtering."""
    field1: str | None = None
    q: str | None = None  # Text search
```

#### 2. Routes (FastAPI)

**Location**: `backend/api/routes/{entity}.py`

**Pattern to follow** (see `backend/api/routes/tests.py`):
- Use dependency injection for `mongo`, `auth`, etc.
- Return Pydantic models with `response_model_by_alias=False`
- Use proper HTTP status codes (404, 409, 400, etc.)
- Include docstrings explaining what each endpoint does

**Standard endpoints**:
```python
@router.post("/entities")           # Create
@router.get("/entities")            # List with filtering
@router.get("/entities/{id}")       # Get single
@router.put("/entities/{id}")       # Update
@router.delete("/entities/{id}")    # Delete
```

#### 3. Validation

- Validate in Pydantic models when possible
- Add custom validation logic in route handlers
- Refer to domain model for all validation rules
- Return meaningful error messages

#### 4. MongoDB Operations

- Use `mongo.collection_name.find()`, `find_one()`, `insert_one()`, `update_one()`
- Always use `{"_id": entity_id}` as the query filter
- Use `ReturnDocument.AFTER` for updates to get updated document

#### 5. Tests

**Location**: `backend/tests/test_{entity}.py`

**Pattern to follow** (see existing test files):
- Use pytest with fixtures
- Test happy paths and error cases
- Test validation logic
- Use `conftest.py` for shared fixtures

### Frontend Patterns

#### 1. Models (Dataclasses)

**Location**: `frontend/app/models.py`

**Pattern to follow** (see existing models):
```python
@dataclasses.dataclass(kw_only=True)
class Entity:
    entity_id: str
    # ... fields matching backend model
    created_at: datetime
    updated_at: datetime
```

#### 2. Pages and Components

**Location**: `frontend/app/`

- Next.js App Router structure with page.tsx files
- Server and client components using React
- Shared components in `frontend/components/`
- Use TypeScript for type safety
- Follow React hooks patterns for state management

## Key Domain Rules (from domain_model_requirements.md)

### Device (Device Under Test)

- **sample_id is derived**: `{sample_type}` or `{sample_type}-{sample_nr}` if sample_nr present
- **Immutable fields** after creation: `device_id`, `manufacturer`, `product_category`, `product_name`, `product_type`, `product_variant`, `sample_nr`, `hardware_link`, `created_at`, `creator`
- **Refrigerant validation**:
  - If `ProductCategory.requires_refrigerant = true` → require refrigerant fields
  - If `refrigerant.circuit_ready = true` → require `medium` and `amount_kg`
- **Journal entries**: Auto-create on every Device create/update; store full JSON snapshot in `data` field

### Test

- **Multiple Devices**: Test has `devices: list[{device_id, device_version}]` (required, at least one)
- **Status transitions**: `draft` → `in_progress` → `finished`
- **Versioning**: When Test starts (`in_progress`), capture latest `device_version` for each Device and `environment_version` for Environment

### Safety Requirements

- **Template scoped by product_category**
- **Results per Device**: Each Device can have multiple `SafetyRequirementResult` records
- **Operations status**:
  - `attended_operation = true` when all required safety requirements are checked
  - `unattended_operation = true` when ALL safety requirements are checked

### Lookup Tables

These are **read-only in Phase 2** (no CRUD APIs):
- `SampleTypes`
- `Location`
- `ProductCategory`
- `Product`
- `RefrigerantMedia`
- `SafetyRequirementTemplate`

Maintained manually by admin directly in MongoDB.

## How to Use This File

### For Implementation

When Claude Code is asked to implement a feature:

1. **Check progress first** - Review [`docs/phase2_implementation_plan.md`](./docs/phase2_implementation_plan.md) to understand current status
2. **Read the domain model** section for that entity
3. **Follow the coding patterns** from existing similar entities
4. **Implement models first**, then routes, then tests
5. **Update progress** - Mark tasks as completed in the implementation plan
6. **Validate against domain rules**

### Simple Prompts

With this context file, you can use simple prompts like:

- "Implement the Device entity"
- "Add journal routes for Device"
- "Create safety requirements endpoints"
- "Update Test model to support multiple Devices"

Claude Code will automatically:
- Read the domain model for specifications
- Follow existing patterns from the codebase
- Apply the validation rules
- Create tests following the existing test patterns

### For Customer (Sub-Phase 2.3)

When implementing Environment:

**Prompt**: "Implement the Environment entity following the same pattern as Device"

Claude Code will:
- Read `domain_model_requirements.md` for Environment specifications
- Follow the Device implementation pattern
- Create models, routes, tests, and frontend views
- Apply journaling pattern from Device

## Git Workflow

- Branch: `phase2` (current development branch)
- Main branch: `main`
- Commit messages: Follow conventional commit style
- Create commits after each logical feature completion

## Testing

### When to Run Tests

**MANDATORY** - Run tests in these scenarios:
1. **After modifying test files** (`backend/tests/` or `conftest.py`)
2. **After backend code changes** (models, routes, validation logic)
3. **Before committing any changes**
4. **When implementation affects existing functionality**

### Commands

```bash
# Run all backend tests (inside Docker container)
docker compose exec backend pytest -v

# Run specific test file
docker compose exec backend pytest tests/test_devices.py -v

# Run tests matching pattern
docker compose exec backend pytest -k "device" -v

# Alternative: Run tests outside Docker with testcontainers
# (Requires Python 3.13+ and uv installed - see docs/LOCAL_DEVELOPMENT.md)
cd backend && uv run pytest -v
```

## Local Development

For detailed local development workflows, see: [`docs/LOCAL_DEVELOPMENT.md`](./docs/LOCAL_DEVELOPMENT.md)

### Quick Reference

**Start All Services** (fully local setup):
```bash
./scripts/dev.sh start
```
- Frontend: http://localhost:3000
- Backend:  http://localhost:8080
- API Docs: http://localhost:8080/

**Backend Testing** (automated with testcontainers):
```bash
cd backend && uv run pytest -v
```

**Note:** All services run locally in Docker. No cloud dependencies needed for development.

### Fully Local Docker Development

The local development environment runs **all services in Docker** (frontend, backend, MongoDB, InfluxDB, Config API). This provides a complete, isolated environment with zero cloud dependencies.

**Getting Started**:

```bash
# Start all services
./scripts/dev.sh start

# View status
./scripts/dev.sh status

# View logs
./scripts/dev.sh logs backend
./scripts/dev.sh logs-f frontend

# Stop all services
./scripts/dev.sh stop
```

**🔥 Hot Reload Enabled:**
- **Frontend** and **Backend** auto-reload when you save code changes (2-5 seconds)
- **NO restart needed** for TypeScript/React/Python code changes
- Watch logs to see: `WatchFiles detected changes... Reloading...`
- **Only rebuild** after adding/removing packages or Dockerfile changes

```bash
# Rebuild after dependency changes only
./scripts/dev.sh rebuild
```

**How It Works**:
- `LOCAL_DEV_MODE=true` - Backend automatically uses local auth and services
- `API_AUTH_ACTIVE=false` - No authentication checks in backend
- `NEXT_PUBLIC_LOCAL_DEV_MODE=true` - Frontend bypasses all authentication
- All services communicate via Docker internal networking
- Frontend → `http://backend:8080`, Backend → `http://config-api:8001`
- Automatic hot reload for both frontend and backend
- "Local Dev" badge shown in UI header when in local development mode

### Production Authentication (Quix Cloud Only)

When deployed to Quix Cloud, the frontend uses **Quix Cloud Plugin Authentication**. This is NOT used in local development.

**Production (Embedded in Quix Portal):**
1. Frontend runs embedded in Quix Portal iframe
2. Requests auth token from parent window via postMessage API
3. Portal responds with fresh token
4. Token stored in React context (not localStorage)
5. Automatic token refresh by Portal

**Standalone Mode (Direct Browser Access):**
1. Frontend detects it's not in an iframe
2. Checks for token in localStorage (`quix_standalone_auth_token`)
3. If no token, displays `AuthTokenDialog` for manual entry
4. Token validated via test API call
5. On 401/403 errors, token cleared and user re-prompted

**Local Development:**
- Backend: Uses `LocalAuth` (mock authentication) when `LOCAL_DEV_MODE=true`
- Frontend: Bypasses authentication when `NEXT_PUBLIC_LOCAL_DEV_MODE=true`
- No tokens required
- All requests automatically allowed
- "Local Dev" badge displayed in UI header
- See `backend/api/local_auth.py` and `frontend/lib/contexts/quix-auth-context.tsx`

**Implementation:**
- `frontend/lib/contexts/quix-auth-context.tsx` - Handles iframe and standalone modes
- `frontend/components/auth/auth-token-dialog.tsx` - Manual token entry
- `backend/api/auth.py` - Conditional import (LocalAuth vs Quix Auth)
- `quix.yaml` - `plugin.embeddedView: true` enables Portal integration


## Database Migrations

### Phase 1 to Phase 2 Migration

**Location**: `migrations/phase1_to_phase2/`

A complete migration system is available to convert existing Phase 1 databases to Phase 2 schema.

**Key changes:**
- Converts `sample_id: string` → `devices: array[{device_id, device_version}]`
- Renames `environment_id` → `environment_id`
- Seeds lookup tables (sample_types, locations)

**Quick Start:**
```bash
cd migrations/phase1_to_phase2

# Preview changes (dry-run)
./run_migration.sh --dry-run

# Execute migration
./run_migration.sh --execute

# Rollback if needed
./run_migration.sh --rollback YYYYMMDD_HHMMSS
```

**Documentation:**
- [`migrations/phase1_to_phase2/QUICK_START.md`](./migrations/phase1_to_phase2/QUICK_START.md) - Quick reference
- [`migrations/phase1_to_phase2/README.md`](./migrations/phase1_to_phase2/README.md) - Complete guide
- [`migrations/phase1_to_phase2/TEST_RESULTS.md`](./migrations/phase1_to_phase2/TEST_RESULTS.md) - Test verification

**When to use:**
- Only needed if migrating from Phase 1 production database
- Skip if starting fresh with Phase 2
- All scripts are tested and production-ready

## Quix Cloud Deployment

This project is deployed to **Quix Cloud**.

**IMPORTANT: Always use the Quix Portal API (curl) instead of the Quix CLI.** The CLI has reliability issues (hangs, requires interactive input, fails in non-interactive mode). The Portal API is faster and more reliable.

### Known Workspace

- **Workspace ID**: `quixers-testmanager-standalone`
- **Branch**: `second-iteration`
- **Backend public URL**: `https://backend-api-quixers-testmanager-standalone.az-france-0.app.quix.io`

### Portal API Usage

All Portal API calls require these headers:
```bash
-H "Authorization: Bearer $TOKEN" -H "X-Version: 2.0"
```

The user will provide the token when needed. Store it in a variable:
```bash
TOKEN="<token-from-user>"
```

#### List Workspaces

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Version: 2.0" \
  "https://portal-api.cloud.quix.io/workspaces" | python3 -m json.tool
```

#### List Deployments

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Version: 2.0" \
  "https://portal-api.cloud.quix.io/workspaces/quixers-testmanager-standalone/deployments" | python3 -m json.tool
```

#### Get Deployment Logs

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Version: 2.0" \
  "https://portal-api.cloud.quix.io/workspaces/quixers-testmanager-standalone/deployments/<deployment-id>/logs" | python3 -m json.tool
```

#### Sync Environment

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "X-Version: 2.0" \
  "https://portal-api.cloud.quix.io/workspaces/quixers-testmanager-standalone/sync" | python3 -m json.tool
```

#### Call Backend API Directly

The backend's public URL can be called directly (requires auth token):
```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://backend-api-quixers-testmanager-standalone.az-france-0.app.quix.io/api/v1/settings"
```

### Deployment Workflow

1. **Commit changes** locally
2. **Push to git**: `git push origin <branch-name>`
3. **Sync Quix deployments** via Portal API (see above) or Portal UI ("Sync" button)
4. **Monitor deployment** by checking logs via Portal API

**Important Notes**:
- Quix environments do NOT automatically sync with git — you must trigger sync
- Don't commit `.claude/` directory — it contains local configuration

## Agent-Specific Guides

For specialized tasks, comprehensive agent context guides are available in [`docs/agents/`](./docs/agents/):

**🧪 [Frontend Test Engineer](./docs/agents/frontend-test-engineer.md)**
- Execute and manage frontend E2E tests using Playwright in Docker
- Token refresh testing strategies and validation
- Troubleshooting test failures
- Writing new E2E tests

**When to use specialized guides:**
- When performing complex, multi-step specialized tasks (testing, deployment, code review)
- When you need detailed procedures and command references for a specific domain
- When troubleshooting domain-specific issues

**How AI agents should use these guides:**
```typescript
// Example: Invoke frontend test engineer via Task tool
Task({
  subagent_type: "general-purpose",
  description: "Run frontend E2E tests",
  prompt: `
    Read docs/agents/frontend-test-engineer.md and execute the token refresh
    E2E tests following the documented Docker-based strategy.
    Report test results with pass/fail status and any recommendations.
  `
})
```

See [`docs/agents/README.md`](./docs/agents/README.md) for the complete index and usage guide.

## Additional Resources

- Domain Model: [`docs/domain_model_requirements.md`](./docs/domain_model_requirements.md)
- Implementation Plan: [`docs/phase2_implementation_plan.md`](./docs/phase2_implementation_plan.md)
- Migrations: [`migrations/README.md`](./migrations/README.md)
- Backend API: FastAPI auto-generates docs at `/docs` when running
- Frontend Framework: [Next.js Documentation](https://nextjs.org/docs) | [React Documentation](https://react.dev)
- **Quix CLI Reference**: https://quix.io/docs/quix-cli/cli-commands-summary.html

## Important Notes

- **Always validate against domain_model_requirements.md before implementing**
- **Follow existing patterns** - consistency is key
- **Test after every change**
- **Document complex logic** with docstrings
- **Ask for clarification** if domain model is unclear
- Never put "Generated with [Claude Code]" or any reference to Claude author on the commits
- Summarize the commit descriptions
- Keep the AI.md file simple and easy to consum for a human. Don't add summaries or anything making more difficult to consume.
- Simplify the commits descriptions in a way that we don't lose important information, but you don't need to describe line by line the changes on the code.