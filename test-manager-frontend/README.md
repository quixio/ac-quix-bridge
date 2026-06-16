# Test Manager - Next.js Frontend

Modern TypeScript + React frontend for the Test Manager system.

## Quick Start (Local Development)

### First Time Setup

```bash
# Start all services (includes frontend, backend, databases)
./scripts/dev.sh start
```

Visit: **http://localhost:3000**

### Daily Development

```bash
# Start all services
./scripts/dev.sh start

# View logs
./scripts/dev.sh logs frontend
./scripts/dev.sh logs-f frontend  # Follow logs

# Restart (after code changes, if needed)
./scripts/dev.sh restart

# Rebuild (after dependency changes)
./scripts/dev.sh rebuild

# Stop all services
./scripts/dev.sh stop
```

## What's Included

- ✅ **Next.js 14** with App Router
- ✅ **TypeScript** for type safety
- ✅ **Tailwind CSS** for styling
- ✅ **shadcn/ui** components (Radix UI + Tailwind)
- ✅ **Quix blue theme** (#0064ff)
- ✅ **Docker-first** development (no Node.js required on host)
- ✅ **Hot reload** enabled

## Project Structure

```
frontend/
├── app/                    # Next.js App Router pages
│   ├── layout.tsx         # Root layout
│   ├── page.tsx           # Home page
│   └── globals.css        # Global styles
├── components/
│   └── ui/                # shadcn/ui components
├── lib/
│   ├── api/               # API client
│   ├── utils/             # Utility functions
│   └── hooks/             # Custom React hooks
├── types/                 # TypeScript type definitions
├── public/                # Static assets
├── Dockerfile.dev         # Development Docker image
└── package.json           # Dependencies

```

## Environment Variables

**Local Development**: No environment variables needed! All services are configured automatically via `docker-compose.yml`.

**Production (Quix Cloud)**: Environment variables are managed through the Quix Portal deployment configuration.

## Development Commands

See [`docs/LOCAL_DEVELOPMENT.md`](../docs/LOCAL_DEVELOPMENT.md) for complete documentation.

Quick reference:

```bash
# Start all services (frontend, backend, databases)
./scripts/dev.sh start

# View logs
./scripts/dev.sh logs frontend
./scripts/dev.sh logs-f frontend    # Follow logs

# Restart after code changes
./scripts/dev.sh restart

# Rebuild after dependency changes
./scripts/dev.sh rebuild

# View status
./scripts/dev.sh status

# Open shell in frontend container
./scripts/dev.sh shell frontend

# Stop all services
./scripts/dev.sh stop
```

## Running the Frontend

The Next.js frontend runs on: **http://localhost:3000**

Backend API available at: **http://localhost:8080**

```bash
# Start everything
./scripts/dev.sh start
```

## Documentation

See `docs/frontend/` for comprehensive guides (if available):

- Migration guides
- Development workflows
- API integration patterns
- Component documentation

## Tech Stack

| Technology     | Version | Purpose           |
| -------------- | ------- | ----------------- |
| Next.js        | 14.2.5  | React framework   |
| React          | 18.3.1  | UI library        |
| TypeScript     | 5.3.3   | Type safety       |
| Tailwind CSS   | 3.4.0   | Styling           |
| shadcn/ui      | Latest  | Component library |
| TanStack Table | 8.21.3  | Data tables       |
| Lucide React   | 0.300.0 | Icons             |

## E2E Testing with Playwright

This project uses Playwright for end-to-end testing. Tests run inside the Docker container.

### Setup (One-time)

Install Playwright browsers in the container:

```bash
docker compose exec frontend npm run test:e2e:install
```

### Running Tests

```bash
# Run all tests (headless)
docker compose exec frontend npm run test:e2e

# Run tests with UI (requires X11 forwarding)
docker compose exec frontend npm run test:e2e:ui

# Run tests in debug mode
docker compose exec frontend npm run test:e2e:debug

# View test report
docker compose exec frontend npm run test:e2e:report
```

### Test Files

- `e2e/tests.spec.ts` - Main test suite for Phase 3 (Tests CRUD)
- `e2e/fixtures.ts` - Shared test fixtures and helpers
- `playwright.config.ts` - Playwright configuration

### What's Tested

**Tests Management (Phase 3):**

- ✅ Tests list page display and navigation
- ✅ Test filtering by status, campaign, Environment ID
- ✅ Test search functionality
- ✅ Test creation with form validation
- ✅ Device selection and search
- ✅ Test editing workflow
- ✅ Test deletion with confirmation
- ✅ Form validation errors
- ✅ Toast notifications

### Writing New Tests

Add new test files to the `e2e/` directory:

```typescript
import { test, expect } from "./fixtures";

test.describe("My Feature", () => {
  test("should do something", async ({ page }) => {
    await page.goto("/my-page");
    await expect(page.getByRole("heading")).toBeVisible();
  });
});
```

### Prerequisites for Tests

- Frontend container must be running (`http://localhost:3000`)
- Backend API must be accessible
- Valid auth token in environment variables
- At least one Device should exist in the database for Device picker tests

## Current Status

**Phase 1: Project Setup & Foundation** ✅ Complete
**Phase 2: Tests List & Detail** ✅ Complete
**Phase 3: Tests CRUD Operations** ✅ Complete
**Phase 4: Devices List & Detail** ✅ Complete
**Phase 5: Devices CRUD Operations** ✅ Complete

- [x] Docker setup
- [x] Next.js initialized
- [x] Tailwind configured
- [x] Base components copied
- [x] Project structure created
- [x] Tests list and detail pages
- [x] Test creation, editing, deletion
- [x] Devices list and detail pages with journal timeline
- [x] Device creation and editing with comprehensive form
- [x] Product hierarchy selection
- [x] Automatic journal entry generation
- [x] Manual journal entry creation
- [x] E2E testing with Playwright

**Next**: Phase 6 - Test-Device Relationships (Multi-Device picker)

## Troubleshooting

### Port already in use

```bash
# Check what's using port 3000
lsof -ti:3000

# Stop all services
./scripts/dev.sh stop
```

### Dependencies not installing

```bash
# Rebuild Docker image
./scripts/dev.sh rebuild
```

### Changes not reflecting

```bash
# Restart all services
./scripts/dev.sh restart
```

---

**Last Updated**: 2025-10-23
**Status**: Phase 1 Complete - Ready for Development
