# Frontend Test Engineer - E2E Testing Guide

**Role**: Specialized AI agent for executing and managing frontend E2E tests using Playwright in Docker containers.

**Purpose**: This guide provides comprehensive context for running, debugging, and maintaining frontend end-to-end tests for the Test Manager System.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites & Setup](#prerequisites--setup)
3. [Test Execution Workflows](#test-execution-workflows)
4. [Test Coverage](#test-coverage)
5. [Token Refresh Testing](#token-refresh-testing)
6. [Troubleshooting](#troubleshooting)
7. [Writing New Tests](#writing-new-tests)
8. [Commands Reference](#commands-reference)

## Overview

### Architecture

The Test Manager System uses **Docker-based E2E testing** with Playwright:

```
┌─────────────────────────────────────────────┐
│  Docker Container: frontend                  │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │ Next.js App (http://localhost:3000)  │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │ Playwright + Chromium Browser         │   │
│  │ - Tests in e2e/ directory             │   │
│  │ - Mock Quix Cloud Plugin auth         │   │
│  │ - Automated UI interactions           │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  Volume: frontend_playwright_cache          │
│  (Persistent browser installations)          │
└─────────────────────────────────────────────┘
         │
         │ API Calls
         ▼
┌─────────────────────────┐
│ Backend API             │
│ (Cloud or Local)        │
└─────────────────────────┘
```

### Key Benefits

1. **No Host Dependencies**: All Playwright browsers and system libraries are pre-installed in Docker container
2. **Consistent Environment**: Same test environment for all developers and CI/CD
3. **Isolated Execution**: Tests run in container without affecting host system
4. **Cached Installations**: Playwright browsers cached in Docker volume for faster execution

### When to Run Tests

Run E2E tests in these scenarios:

- ✅ After implementing new features
- ✅ After modifying authentication/token refresh logic
- ✅ After changing UI components or navigation
- ✅ Before creating pull requests
- ✅ When debugging unexpected UI behavior
- ✅ After updating dependencies that affect frontend

## Prerequisites & Setup

### Environment Requirements

**For Local Development (Docker Compose):**
- Frontend container (`frontend`) must be running
- Backend API running locally (no auth tokens needed - uses `LOCAL_DEV_MODE=true`)
- Frontend accessible at `http://localhost:3000`

**For Cloud Backend Testing:**
- Frontend container must be running with environment variables configured
- Backend API must be accessible in Quix Cloud
- Valid `QUIX_AUTH_TOKEN` environment variable set

**Check frontend container status:**
```bash
docker compose ps frontend
```

**Expected output:**
```
NAME                          STATUS    PORTS
frontend                 Up        0.0.0.0:3000->3000/tcp
```

### One-Time Setup: Install Playwright Browsers

Before running tests for the first time, install Playwright browsers in the container:

```bash
docker compose exec frontend npm run test:e2e:install
```

**What this does:**
- Installs Chromium browser inside the Docker container
- Installs system dependencies (libglib2.0-0, libnss3, libx11-6, etc.)
- Caches installation in `frontend_playwright_cache` volume
- Only needs to be run once (or after clearing Docker volumes)

**Verification:**
```bash
docker compose exec frontend npx playwright --version
```

Should output: `Version 1.x.x`

### Test Data Requirements

For comprehensive testing, ensure the following data exists in the database:

- **At least one Device**: Required for Device picker tests
- **At least one Test**: Required for test detail/edit page tests
- **Valid auth token**: Required for authenticated API calls

**Quick way to seed test data:**

Use the admin seed data dialog in the UI or make an API call:
```bash
curl -X POST http://localhost:3000/api/v1/admin/seed-test-data \
  -H "Authorization: Bearer $QUIX_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"num_dacs": 5, "num_tests": 10}'
```

## Test Execution Workflows

### Standard Test Run (Headless)

**Most common usage** - runs all tests in headless mode:

```bash
docker compose exec frontend npm run test:e2e
```

**Output:**
```
Running 8 tests using 1 worker

  ✓  1 token-refresh.spec.ts:56:11 › should initialize token on page load (500ms)
  ✓  2 token-refresh.spec.ts:69:11 › should refresh token when visibility changes (650ms)
  ✓  3 token-refresh.spec.ts:110:11 › should make authenticated API calls with token (2100ms)
  ...

  8 passed (5.3s)
```

### Run Specific Test File

```bash
docker compose exec frontend npx playwright test token-refresh.spec.ts
```

### Run Specific Test Case

```bash
docker compose exec frontend npx playwright test -g "should initialize token"
```

The `-g` flag uses regex matching on test names.

### UI Mode (Interactive Debugging)

**Note:** Requires X11 forwarding (Linux/WSL) or may not work in some environments.

```bash
docker compose exec frontend npm run test:e2e:ui
```

**What UI mode provides:**
- Visual browser window showing test execution
- Step-by-step test navigation
- Time-travel debugging
- DOM inspector

**If UI mode doesn't work:**
- Use debug mode instead
- Check X11 forwarding is configured
- Consider running tests headless with screenshots on failure

### Debug Mode

Runs tests with Playwright Inspector for step-by-step debugging:

```bash
docker compose exec frontend npm run test:e2e:debug
```

**Debug capabilities:**
- Pause test execution
- Step through actions
- Inspect element selectors
- View console logs
- Take screenshots at any point

### Generate and View Test Report

After test execution, generate an HTML report:

```bash
docker compose exec frontend npm run test:e2e:report
```

**Report includes:**
- Test pass/fail summary
- Screenshots of failures
- Videos of test runs (if enabled)
- Detailed step-by-step traces
- Network activity logs

## Test Coverage

### Current Test Suites

#### 1. Token Refresh Tests (`e2e/token-refresh.spec.ts`)

Validates Quix Cloud Plugin authentication with comprehensive token refresh scenarios.

**Test Cases:**

| Test Name | Scenario | Validation |
|-----------|----------|------------|
| `should initialize token on page load` | App receives initial token via postMessage | Token stored in localStorage |
| `should refresh token when visibility changes` | Tab becomes visible after being hidden | New token fetched and different from initial |
| `should make authenticated API calls with token` | API requests include Authorization header | Header present in network requests |
| `should handle expired token scenario` | 401 response triggers retry with fresh token | Request retried after token refresh |
| `should store token in localStorage for persistence` | Token survives page reloads | Token present after reload |
| `should send auth request on manual refresh trigger` | postMessage request sent | Auth request captured |
| `should retry failed request after token refresh` | API fails with 401, then succeeds | 2 requests total (original + retry) |
| `should not retry more than once per request` | API always returns 401 | Max 2 requests (no infinite loop) |
| `should handle 403 forbidden errors with token refresh` | 403 response triggers retry | Retry with fresh token succeeds |

**What's validated:**
- ✅ Token initialization from Quix Cloud Plugin
- ✅ Automatic token refresh on visibility change
- ✅ On-demand token refresh on auth errors (401/403)
- ✅ Token persistence in localStorage
- ✅ Retry logic with fresh token
- ✅ Prevention of infinite retry loops

#### 2. Application Tests (`e2e/tests.spec.ts`)

Validates core application functionality for test management workflows.

**Test Cases:**

- **Tests List Page**
  - Display and navigation
  - Filtering by status, campaign, Environment ID
  - Search functionality
  - Pagination

- **Test Creation**
  - Form validation
  - Device selection and search
  - Required field validation
  - Successful creation

- **Test Editing**
  - Load existing test data
  - Update fields
  - Save changes
  - Validation

- **Test Deletion**
  - Confirmation dialog
  - Successful deletion
  - Navigation after delete

- **Device Picker**
  - Device search
  - Version display
  - Selection behavior

**What's validated:**
- ✅ Complete CRUD operations for tests
- ✅ Form validation and error handling
- ✅ Navigation flows
- ✅ Toast notifications
- ✅ Search and filter functionality

### Test File Structure

```
frontend/e2e/
├── fixtures.ts              # Shared test fixtures and setup
├── tests.spec.ts            # Application functionality tests
└── token-refresh.spec.ts    # Authentication and token refresh tests
```

## Token Refresh Testing

### Authentication Architecture

The frontend uses the **Quix Cloud Plugin** authentication pattern with three refresh strategies:

```typescript
┌─────────────────────────────────────────────────────────────┐
│  QuixAuthProvider (React Context)                            │
│                                                               │
│  Token Refresh Strategies:                                   │
│                                                               │
│  1. PERIODIC (Every 30 minutes)                              │
│     setInterval(() => refreshToken(), 30 * 60 * 1000)        │
│                                                               │
│  2. VISIBILITY-BASED (When tab becomes visible)              │
│     document.addEventListener('visibilitychange')            │
│                                                               │
│  3. ON-DEMAND (On 401/403 errors)                            │
│     API client detects auth failure → refreshToken()         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Quix Cloud Plugin (postMessage API)                         │
│                                                               │
│  App sends:  { type: 'quix-auth-request' }                   │
│  Plugin responds: { type: 'quix-auth-response', token }      │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### How Token Refresh Works in Tests

E2E tests **mock the Quix Cloud Plugin** using `beforeEach` hooks:

```typescript
test.beforeEach(async ({ page, context }) => {
  await context.addInitScript(() => {
    let mockToken = 'mock-token-initial-12345';
    let tokenRefreshCount = 0;

    // Listen for token requests from the app
    window.addEventListener('message', (event) => {
      if (event.data.type === 'quix-auth-request') {
        tokenRefreshCount++;
        mockToken = `mock-token-refresh-${tokenRefreshCount}-${Date.now()}`;

        window.postMessage({
          type: 'quix-auth-response',
          token: mockToken,
        }, '*');
      }
    });

    // Send initial token on page load
    setTimeout(() => {
      window.postMessage({
        type: 'quix-auth-response',
        token: mockToken,
      }, '*');
    }, 100);
  });
});
```

**Mock behavior:**
1. **Initial token** sent automatically 100ms after page load
2. **Refresh requests** trigger increment counter and generate new token
3. **Tokens include timestamp** to ensure uniqueness
4. **Full postMessage API** is simulated in browser context

### Key Files for Token Refresh

| File | Purpose | Key Functionality |
|------|---------|-------------------|
| `frontend/lib/contexts/quix-auth-context.tsx` | Auth provider | Token state, 3 refresh strategies, debouncing |
| `frontend/lib/hooks/use-api.ts` | API hooks | Auto-inject token into all API calls |
| `frontend/lib/api/client.ts` | HTTP client | Retry logic on 401/403 with token refresh |
| `frontend/e2e/token-refresh.spec.ts` | E2E tests | Validate all refresh scenarios |

### Testing Token Refresh Manually

If you need to manually test token refresh behavior:

1. **Open browser DevTools** in the running app
2. **Clear token**: `localStorage.removeItem('quix_auth_token')`
3. **Monitor postMessage**:
   ```javascript
   window.addEventListener('message', (e) => console.log('Message:', e.data))
   ```
4. **Trigger refresh**: Switch tabs (visibility change) or wait 30 minutes (periodic)
5. **Verify new token**: `localStorage.getItem('quix_auth_token')`

## Troubleshooting

### Container Not Running

**Symptom:**
```
Error: Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

**Solution:**
```bash
# Start the frontend container
docker compose up -d frontend

# Verify it's running
docker compose ps
```

### Playwright Browsers Not Installed

**Symptom:**
```
Error: Executable doesn't exist at /root/.cache/ms-playwright/chromium-1234/chrome-linux/chrome
```

**Solution:**
```bash
# Install Playwright browsers in container
docker compose exec frontend npm run test:e2e:install
```

### Frontend Not Accessible

**Symptom:**
```
Error: page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:3000
```

**Solution:**
```bash
# Check if frontend is running
curl http://localhost:3000

# Check frontend logs
docker compose logs frontend

# Restart frontend if needed
docker compose restart frontend
```

### Authentication Failures in Tests

**Symptom:**
```
Test failed: Expected 200, got 401
```

**Solution:**

**For Local Development:**
1. **Verify LOCAL_DEV_MODE is enabled**:
   ```bash
   docker compose exec backend env | grep LOCAL_DEV_MODE
   # Should show: LOCAL_DEV_MODE=true
   ```

2. **Restart services**:
   ```bash
   ./scripts/dev.sh restart
   ```

**For Cloud Backend Testing:**
1. **Check auth token environment variable**:
   ```bash
   docker compose exec frontend env | grep QUIX_AUTH_TOKEN
   ```

2. **Verify backend is accessible**:
   ```bash
   curl -H "Authorization: Bearer $QUIX_AUTH_TOKEN" $BACKEND_URL/health
   ```

3. **Check if token is expired** - tokens expire every 24 hours. Get fresh token from Quix Portal.

4. **Update environment and rebuild**:
   ```bash
   # Update docker-compose.yml with new token
   docker compose down frontend
   docker volume rm testmanager_frontend_next_cache
   docker compose up -d frontend
   ```

### Tests Timeout

**Symptom:**
```
Test timeout of 30000ms exceeded
```

**Solution:**
1. **Increase timeout** in test file:
   ```typescript
   test.setTimeout(60000); // 60 seconds
   ```

2. **Check backend performance** - slow API responses cause timeouts

3. **Verify network connectivity** between container and backend

4. **Run with headed mode** to see what's happening:
   ```bash
   docker compose exec frontend npx playwright test --headed
   ```

### Volume Permission Issues

**Symptom:**
```
Error: EACCES: permission denied, open '/app/.next/...'
```

**Solution:**
```bash
# Remove and recreate volumes
docker compose down -v
docker compose up -d
```

### Debugging Failed Tests

**Best practices:**

1. **Check test output** for error messages and stack traces

2. **View screenshots** of failures:
   ```bash
   docker compose exec frontend ls -la test-results/
   ```

3. **Enable video recording** in `playwright.config.ts`:
   ```typescript
   use: {
     video: 'on', // or 'retain-on-failure'
   }
   ```

4. **Run single test** with debug mode:
   ```bash
   docker compose exec frontend npx playwright test token-refresh.spec.ts --debug
   ```

5. **Check browser console logs** - Playwright captures them:
   ```typescript
   page.on('console', msg => console.log('Browser log:', msg.text()))
   ```

## Writing New Tests

### Test File Template

```typescript
import { test, expect } from './fixtures';

test.describe('Feature Name', () => {
  test.beforeEach(async ({ page }) => {
    // Setup - navigate to page, set state, etc.
    await page.goto('/path');
  });

  test('should do something', async ({ page }) => {
    // Arrange - setup test data

    // Act - perform actions
    await page.click('button[data-testid="submit"]');

    // Assert - verify results
    await expect(page.locator('[role="alert"]')).toContainText('Success');
  });
});
```

### Best Practices

#### 1. Use Test IDs for Selectors

**Good:**
```typescript
await page.click('[data-testid="create-test-button"]');
```

**Avoid:**
```typescript
await page.click('button.bg-blue-500.hover\\:bg-blue-600'); // Breaks if styles change
```

#### 2. Wait for Elements Properly

**Good:**
```typescript
await page.waitForSelector('[data-testid="test-list"]');
await expect(page.locator('[data-testid="test-item"]')).toHaveCount(5);
```

**Avoid:**
```typescript
await page.waitForTimeout(1000); // Flaky - might be too short or too long
```

#### 3. Mock API Responses When Needed

```typescript
await page.route('**/api/tests', async (route) => {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ items: [], total: 0 }),
  });
});
```

#### 4. Use Fixtures for Common Setup

Add to `e2e/fixtures.ts`:

```typescript
import { test as base } from '@playwright/test';

export const test = base.extend({
  authenticatedPage: async ({ page }, use) => {
    // Setup authentication
    await page.goto('/');
    await page.waitForSelector('[data-testid="dashboard"]');
    await use(page);
  },
});
```

Usage:
```typescript
test('should access protected page', async ({ authenticatedPage }) => {
  // authenticatedPage is already logged in
});
```

#### 5. Organize Tests by Feature

```
e2e/
├── auth/
│   ├── token-refresh.spec.ts
│   └── login.spec.ts
├── tests/
│   ├── create.spec.ts
│   ├── edit.spec.ts
│   └── list.spec.ts
└── dacs/
    ├── create.spec.ts
    └── journal.spec.ts
```

#### 6. Test Both Happy Path and Error Cases

```typescript
test('should create test successfully', async ({ page }) => {
  // Happy path
});

test('should show validation error for missing fields', async ({ page }) => {
  // Error case
});

test('should handle API error gracefully', async ({ page }) => {
  // Error case - API failure
});
```

### Common Patterns

#### Testing Form Submission

```typescript
test('should submit form', async ({ page }) => {
  await page.fill('[name="test_id"]', 'TEST-001');
  await page.fill('[name="campaign_id"]', 'CAMPAIGN-001');
  await page.click('[data-testid="submit-button"]');

  await expect(page).toHaveURL('/tests/TEST-001');
  await expect(page.locator('[role="alert"]')).toContainText('Test created');
});
```

#### Testing Navigation

```typescript
test('should navigate to test details', async ({ page }) => {
  await page.goto('/tests');
  await page.click('[data-testid="test-item-TEST-001"]');

  await expect(page).toHaveURL('/tests/TEST-001');
  await expect(page.locator('h1')).toContainText('TEST-001');
});
```

#### Testing Search/Filter

```typescript
test('should filter tests by status', async ({ page }) => {
  await page.goto('/tests');
  await page.selectOption('[name="status"]', 'in_progress');

  await page.waitForResponse(resp => resp.url().includes('/api/tests'));

  const items = page.locator('[data-testid="test-item"]');
  await expect(items).toHaveCount(3);

  for (let i = 0; i < await items.count(); i++) {
    await expect(items.nth(i)).toContainText('In Progress');
  }
});
```

#### Testing Modal Dialogs

```typescript
test('should show delete confirmation', async ({ page }) => {
  await page.click('[data-testid="delete-button"]');

  const dialog = page.locator('[role="dialog"]');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Are you sure?');

  await dialog.locator('[data-testid="confirm-button"]').click();

  await expect(dialog).not.toBeVisible();
  await expect(page.locator('[role="alert"]')).toContainText('Deleted');
});
```

## Commands Reference

### Quick Lookup Table

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `docker compose exec frontend npm run test:e2e` | Run all tests (headless) | Standard test execution, CI/CD |
| `docker compose exec frontend npm run test:e2e:ui` | Run with UI | Interactive debugging, visual inspection |
| `docker compose exec frontend npm run test:e2e:debug` | Run with Playwright Inspector | Step-by-step debugging |
| `docker compose exec frontend npm run test:e2e:report` | View test report | After test run, analyze failures |
| `docker compose exec frontend npm run test:e2e:install` | Install Playwright browsers | First-time setup, after volume clear |
| `npx playwright test <file>` | Run specific file | Focus on one test suite |
| `npx playwright test -g "<pattern>"` | Run tests matching pattern | Test specific scenarios |
| `npx playwright test --headed` | Run with visible browser | See what's happening visually |
| `npx playwright test --project=chromium` | Run on specific browser | Browser-specific testing |

### Environment Variables

| Variable | Purpose | Required | Example |
|----------|---------|----------|---------|
| `QUIX_AUTH_TOKEN` | Backend API authentication | Yes | `eyJhbGc...` |
| `BACKEND_URL` | Backend API base URL | Yes | `https://backend-api-....quix.io` |
| `NEXT_PUBLIC_QUIX_AUTH_TOKEN` | Client-side token (embedded at build time) | Yes | Same as `QUIX_AUTH_TOKEN` |

### Container Management

| Command | Purpose |
|---------|---------|
| `docker compose ps` | Check container status |
| `docker compose logs frontend` | View frontend logs |
| `docker compose restart frontend` | Restart frontend |
| `docker compose exec frontend bash` | Enter container shell |
| `docker compose down -v` | Stop and remove volumes |

## Related Documentation

- **[CLAUDE.md](../../CLAUDE.md)** - Main project context and development guide
- **[frontend/README.md](../../frontend/README.md)** - Frontend-specific documentation and setup
- **[LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md)** - Local development workflows
- **[Playwright Documentation](https://playwright.dev)** - Official Playwright docs
- **[Next.js Testing](https://nextjs.org/docs/testing)** - Next.js testing best practices

## Summary for AI Agents

When invoked as a frontend test engineer:

1. **Verify prerequisites** - container running, token valid, backend accessible
2. **Run tests** - Use `docker compose exec frontend npm run test:e2e`
3. **Analyze results** - Check for failures, review screenshots/videos if available
4. **Report findings** - Provide clear summary of pass/fail status and any issues
5. **Suggest fixes** - If failures occur, provide specific recommendations based on error messages

**Key success criteria:**
- All tests pass ✅
- No timeout errors
- Token refresh scenarios work correctly
- No console errors during test execution

**If tests fail:**
- Provide specific failing test names
- Include error messages and stack traces
- Suggest probable causes based on error patterns
- Recommend next debugging steps
