# API Client Guide - Next.js Frontend

This document provides comprehensive guidance on API integration patterns for the Next.js frontend, including the API client architecture, TypeScript types, error handling, and best practices.

---

## Table of Contents

1. [API Client Architecture](#api-client-architecture)
2. [Base API Client](#base-api-client)
3. [TypeScript Types](#typescript-types)
4. [API Service Modules](#api-service-modules)
5. [Error Handling](#error-handling)
6. [Authentication](#authentication)
7. [Usage Patterns](#usage-patterns)
8. [Best Practices](#best-practices)

---

## API Client Architecture

The API client follows the pattern established in Quix.Admin:

```
┌─────────────────────────────────────────┐
│          React Components               │
│   (Pages, Views, Custom Hooks)          │
└────────────────┬────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────┐
│         API Service Modules             │
│  (testsApi, devicesApi, lookupsApi, etc.)  │
└────────────────┬────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────┐
│         Base API Client                 │
│  (apiGet, apiPost, apiPut, apiDelete)   │
└────────────────┬────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────┐
│           Backend REST API              │
│    (FastAPI on Quix Cloud)              │
└─────────────────────────────────────────┘
```

**Key Principles**:
- **Separation of Concerns**: Base client handles HTTP, service modules handle domain logic
- **Type Safety**: Full TypeScript typing for requests and responses
- **Error Handling**: Centralized error handling with custom error classes
- **Authentication**: Automatic token injection
- **URL Management**: Auto-detect API URL based on environment

---

## Base API Client

### Location
`lib/api/client.ts`

### Implementation

```typescript
// lib/api/client.ts

// Custom error class for API errors
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data?: any
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// Get API URL from environment or default
export function getApiUrl(): string {
  // 1. Check explicit environment variable (highest priority)
  if (typeof window !== 'undefined') {
    // Client-side
    if (process.env.NEXT_PUBLIC_API_URL) {
      return process.env.NEXT_PUBLIC_API_URL
    }
  } else {
    // Server-side
    if (process.env.API_URL) {
      return process.env.API_URL
    }
  }

  // 2. Default to localhost for development
  // When deployed to Quix, always set NEXT_PUBLIC_API_URL explicitly
  return 'http://localhost:8080'
}

// Note: Get the backend API URL from Quix Portal:
// 1. Navigate to your Quix environment
// 2. Find "Test Manager - Backend" deployment
// 3. Copy the public URL (e.g., https://backend-api-{workspace-id}.{region}.app.quix.io)
// 4. Set as NEXT_PUBLIC_API_URL environment variable

// Generic API call function
export async function apiCall<T>(
  method: string,
  endpoint: string,
  data?: any,
  options?: RequestInit
): Promise<T> {
  const url = `${getApiUrl()}${endpoint}`

  // Get auth token
  const token = getAuthToken()

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Version': '2.0',
    ...(options?.headers as Record<string, string> || {})
  }

  // Add auth token if available
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const config: RequestInit = {
    method,
    headers,
    ...options
  }

  // Add body for POST, PUT, PATCH
  if (data && ['POST', 'PUT', 'PATCH'].includes(method)) {
    config.body = JSON.stringify(data)
  }

  try {
    const response = await fetch(url, config)

    // Handle 401 Unauthorized (token expired)
    if (response.status === 401) {
      // Attempt token refresh or redirect to login
      handleUnauthorized()
      throw new ApiError('Unauthorized', 401)
    }

    // Handle non-2xx responses
    if (!response.ok) {
      const errorData = await response.json().catch(() => null)
      throw new ApiError(
        errorData?.message || `HTTP ${response.status}: ${response.statusText}`,
        response.status,
        errorData
      )
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return null as T
    }

    // Parse JSON response
    const result = await response.json()
    return result as T

  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }

    // Network errors
    throw new ApiError(
      error instanceof Error ? error.message : 'Network error',
      0
    )
  }
}

// Helper functions for HTTP methods
export function apiGet<T>(endpoint: string, params?: Record<string, any>): Promise<T> {
  // Convert params to query string
  let url = endpoint
  if (params) {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        // Handle pagination: page/pageSize → limit/offset
        if (key === 'page' && params.pageSize) {
          searchParams.append('limit', params.pageSize.toString())
          searchParams.append('offset', ((value - 1) * params.pageSize).toString())
        } else if (key !== 'pageSize') {
          searchParams.append(key, value.toString())
        }
      }
    })
    if (searchParams.toString()) {
      url += `?${searchParams.toString()}`
    }
  }

  return apiCall<T>('GET', url)
}

export function apiPost<T>(endpoint: string, data?: any): Promise<T> {
  return apiCall<T>('POST', endpoint, data)
}

export function apiPut<T>(endpoint: string, data?: any): Promise<T> {
  return apiCall<T>('PUT', endpoint, data)
}

export function apiPatch<T>(endpoint: string, data?: any): Promise<T> {
  return apiCall<T>('PATCH', endpoint, data)
}

export function apiDelete<T = void>(endpoint: string): Promise<T> {
  return apiCall<T>('DELETE', endpoint)
}

// Auth token management
function getAuthToken(): string | null {
  // Try to get from Quix environment (server-side or injected client-side)
  if (typeof window !== 'undefined' && (window as any).QUIX_AUTH_TOKEN) {
    return (window as any).QUIX_AUTH_TOKEN
  }

  // Try localStorage (client-side)
  if (typeof window !== 'undefined') {
    return localStorage.getItem('auth_token')
  }

  // Try environment variable (server-side)
  return process.env.QUIX_AUTH_TOKEN || null
}

function handleUnauthorized() {
  // Clear token
  if (typeof window !== 'undefined') {
    localStorage.removeItem('auth_token')
    // Redirect to login or show error
    window.location.href = '/auth/login'
  }
}
```

---

## TypeScript Types

### Pagination Types

```typescript
// types/api.ts

export interface PaginationParams {
  page?: number
  pageSize?: number
  limit?: number
  offset?: number
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}
```

### Test Types

```typescript
// types/test.ts

export enum TestStatus {
  DRAFT = 'draft',
  IN_PROGRESS = 'in_progress',
  FINISHED = 'finished'
}

export interface DeviceReference {
  dac_id: string
  dac_version: number
}

export interface TestFile {
  filename: string
  size: number
  uploaded_at: string
}

export interface TestLink {
  title: string
  url: string
}

export interface Test {
  test_id: string
  title: string
  description?: string
  status: TestStatus
  tec_id?: string
  tec_version?: number
  campaign_id?: string
  dacs: DeviceReference[]
  files: TestFile[]
  links: TestLink[]
  created_at: string
  updated_at: string
  creator?: string
}

export interface TestCreate {
  test_id: string
  title: string
  description?: string
  status?: TestStatus
  tec_id?: string
  campaign_id?: string
  dacs: string[]  // Array of dac_ids
  links?: TestLink[]
}

export interface TestUpdate {
  title?: string
  description?: string
  status?: TestStatus
  tec_id?: string
  campaign_id?: string
  dacs?: string[]
  links?: TestLink[]
}

export interface TestListParams extends PaginationParams {
  status?: TestStatus
  tec_id?: string
  campaign_id?: string
  q?: string  // Text search
}
```

### Device Types

```typescript
// types/device.ts

export enum DeviceStatus {
  CREATED = 'created',
  SETUP = 'setup',
  STORED = 'stored',
  SCRAPPED = 'scrapped'
}

export enum JournalCategory {
  SAFETY = 'safety',
  REFRIGERANT = 'refrigerant',
  SETUP = 'setup',
  CONFIGURATION = 'configuration',
  MEASUREMENT = 'measurement',
  MAINTENANCE = 'maintenance',
  OTHER = 'other'
}

export interface Refrigerant {
  circuit_ready: boolean
  medium?: string
  amount_kg?: number
}

export interface Device {
  device_id: string
  device_version: number
  manufacturer: string
  product_category: string
  product_name: string
  product_type: string
  product_variant?: string
  sample_type: string
  sample_nr?: string
  sample_id: string  // Derived: {sample_type}-{sample_nr} or {sample_type}
  location: string
  status: DeviceStatus
  hardware_link?: string
  refrigerant?: Refrigerant
  notes?: string
  created_at: string
  updated_at: string
  creator?: string
}

export interface DeviceCreate {
  dac_id: string
  manufacturer: string
  product_category: string
  product_name: string
  product_type: string
  product_variant?: string
  sample_type: string
  sample_nr?: string
  location: string
  status?: DeviceStatus
  hardware_link?: string
  refrigerant?: Refrigerant
  notes?: string
}

export interface DeviceUpdate {
  location?: string
  status?: DeviceStatus
  refrigerant?: Refrigerant
  notes?: string
}

export interface DeviceJournalEntry {
  entry_id: string
  dac_id: string
  category: JournalCategory
  description: string
  timestamp: string
  user?: string
  metadata?: Record<string, any>  // JSON snapshot
}

export interface DeviceJournalEntryCreate {
  category: JournalCategory
  description: string
}

export interface DeviceListParams extends PaginationParams {
  status?: DeviceStatus
  location?: string
  product_category?: string
  q?: string
}
```

### Logbook Types

```typescript
// types/logbook.ts

export interface LogbookEntry {
  entry_id: string
  test_id: string
  timestamp: string
  description: string
  sensor?: string
  created_at: string
  creator?: string
}

export interface LogbookEntryCreate {
  timestamp: string
  description: string
  sensor?: string
}

export interface LogbookEntryUpdate {
  timestamp?: string
  description?: string
  sensor?: string
}
```

### Lookup Types

```typescript
// types/lookup.ts

export interface SampleType {
  sample_type_id: string
  sample_type: string
}

export interface Location {
  location_id: string
  location: string
}
```

---

## API Service Modules

### Tests API

```typescript
// lib/api/tests.ts

import { apiGet, apiPost, apiPut, apiDelete } from './client'
import type {
  Test,
  TestCreate,
  TestUpdate,
  TestListParams,
  PaginatedResponse
} from '@/types'

export const testsApi = {
  /**
   * List tests with optional filtering and pagination
   */
  list: (params?: TestListParams) =>
    apiGet<PaginatedResponse<Test>>('/tests', params),

  /**
   * Get a single test by ID
   */
  get: (testId: string) =>
    apiGet<Test>(`/tests/${testId}`),

  /**
   * Create a new test
   */
  create: (data: TestCreate) =>
    apiPost<Test>('/tests', data),

  /**
   * Update an existing test
   */
  update: (testId: string, data: TestUpdate) =>
    apiPut<Test>(`/tests/${testId}`, data),

  /**
   * Delete a test
   */
  delete: (testId: string) =>
    apiDelete(`/tests/${testId}`)
}
```

### Devices API

```typescript
// lib/api/devices.ts

import { apiGet, apiPost, apiPut, apiDelete } from './client'
import type {
  Device,
  DeviceCreate,
  DeviceUpdate,
  DeviceListParams,
  DeviceJournalEntry,
  DeviceJournalEntryCreate,
  PaginatedResponse
} from '@/types'

export const devicesApi = {
  /**
   * List Devices with optional filtering and pagination
   */
  list: (params?: DeviceListParams) =>
    apiGet<PaginatedResponse<Device>>('/devices', params),

  /**
   * Get a single Device by ID
   */
  get: (deviceId: string) =>
    apiGet<Device>(`/devices/${deviceId}`),

  /**
   * Create a new Device
   */
  create: (data: DeviceCreate) =>
    apiPost<Device>('/devices', data),

  /**
   * Update an existing Device
   * Note: Automatically creates journal entry
   */
  update: (deviceId: string, data: DeviceUpdate) =>
    apiPut<Device>(`/devices/${deviceId}`, data),

  /**
   * Delete a Device
   */
  delete: (deviceId: string) =>
    apiDelete(`/devices/${deviceId}`),

  /**
   * Preview update changes (get suggested journal description)
   */
  previewUpdate: (deviceId: string, data: DeviceUpdate) =>
    apiPost<{ suggested_description: string }>(`/devices/${deviceId}/preview`, data),

  /**
   * Get journal entries for a Device
   */
  getJournal: (deviceId: string) =>
    apiGet<DeviceJournalEntry[]>(`/devices/${deviceId}/journal`),

  /**
   * Create a manual journal entry
   */
  createJournalEntry: (deviceId: string, data: DeviceJournalEntryCreate) =>
    apiPost<DeviceJournalEntry>(`/devices/${deviceId}/journal`, data)
}
```

### Logbook API

```typescript
// lib/api/logbook.ts

import { apiGet, apiPost, apiPut, apiDelete } from './client'
import type {
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate
} from '@/types'

export const logbookApi = {
  /**
   * List all logbook entries for a test
   */
  list: (testId: string) =>
    apiGet<LogbookEntry[]>(`/tests/${testId}/logbook`),

  /**
   * Get a single logbook entry
   */
  get: (testId: string, entryId: string) =>
    apiGet<LogbookEntry>(`/tests/${testId}/logbook/${entryId}`),

  /**
   * Create a new logbook entry
   */
  create: (testId: string, data: LogbookEntryCreate) =>
    apiPost<LogbookEntry>(`/tests/${testId}/logbook`, data),

  /**
   * Update a logbook entry
   */
  update: (testId: string, entryId: string, data: LogbookEntryUpdate) =>
    apiPut<LogbookEntry>(`/tests/${testId}/logbook/${entryId}`, data),

  /**
   * Delete a logbook entry
   */
  delete: (testId: string, entryId: string) =>
    apiDelete(`/tests/${testId}/logbook/${entryId}`)
}
```

### Files API

```typescript
// lib/api/files.ts

import { apiGet, apiDelete } from './client'

export const filesApi = {
  /**
   * Get presigned upload URL for a file
   */
  getUploadUrl: (testId: string, filename: string) =>
    apiGet<{ upload_url: string }>(`/tests/${testId}/files/upload-url`, { filename }),

  /**
   * List all files for a test
   */
  list: (testId: string) =>
    apiGet<{ filename: string; size: number; uploaded_at: string }[]>(`/tests/${testId}/files`),

  /**
   * Get presigned download URL for a file
   */
  getDownloadUrl: (testId: string, filename: string) =>
    apiGet<{ download_url: string }>(`/tests/${testId}/files/${filename}/download-url`),

  /**
   * Delete a file
   */
  delete: (testId: string, filename: string) =>
    apiDelete(`/tests/${testId}/files/${filename}`)
}
```

### Lookups API

```typescript
// lib/api/lookups.ts

import { apiGet } from './client'
import type { SampleType, Location } from '@/types'

export const lookupsApi = {
  /**
   * Get all sample types
   */
  getSampleTypes: () =>
    apiGet<SampleType[]>('/sample-types'),

  /**
   * Get all locations
   */
  getLocations: () =>
    apiGet<Location[]>('/locations')
}
```

---

## Error Handling

### ApiError Class

```typescript
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data?: any
  ) {
    super(message)
    this.name = 'ApiError'
  }

  // Check if error is a specific HTTP status
  is(status: number): boolean {
    return this.status === status
  }

  // Check if error is a validation error (422)
  isValidationError(): boolean {
    return this.status === 422
  }

  // Extract validation errors from 422 response
  getValidationErrors(): Record<string, string[]> | null {
    if (!this.isValidationError() || !this.data?.detail) {
      return null
    }

    // FastAPI validation errors format:
    // { detail: [{ loc: ['field'], msg: 'error message' }] }
    const errors: Record<string, string[]> = {}

    for (const error of this.data.detail) {
      const field = error.loc[error.loc.length - 1]
      if (!errors[field]) {
        errors[field] = []
      }
      errors[field].push(error.msg)
    }

    return errors
  }
}
```

### Error Handling in Components

```tsx
import { useState } from 'react'
import { testsApi } from '@/lib/api/tests'
import { ApiError } from '@/lib/api/client'
import { useToast } from '@/hooks/use-toast'

export function TestForm() {
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (data: TestCreate) => {
    setLoading(true)

    try {
      const test = await testsApi.create(data)

      toast({
        title: 'Test created successfully',
        description: `Test ID: ${test.test_id}`
      })

      router.push(`/tests/${test.test_id}`)

    } catch (error) {
      if (error instanceof ApiError) {
        // Handle validation errors
        if (error.isValidationError()) {
          const validationErrors = error.getValidationErrors()
          // Set form errors
          Object.entries(validationErrors || {}).forEach(([field, messages]) => {
            form.setError(field as any, {
              type: 'manual',
              message: messages[0]
            })
          })
        } else {
          // Handle other API errors
          toast({
            title: 'Failed to create test',
            description: error.message,
            variant: 'destructive'
          })
        }
      } else {
        // Handle unknown errors
        toast({
          title: 'An unexpected error occurred',
          variant: 'destructive'
        })
      }
    } finally {
      setLoading(false)
    }
  }

  // ...
}
```

---

## Authentication

### Token Management

```typescript
// lib/utils/auth.ts

export function getAuthToken(): string | null {
  // Priority 1: Quix environment (injected by platform)
  if (typeof window !== 'undefined' && (window as any).QUIX_AUTH_TOKEN) {
    return (window as any).QUIX_AUTH_TOKEN
  }

  // Priority 2: localStorage (for local dev)
  if (typeof window !== 'undefined') {
    return localStorage.getItem('auth_token')
  }

  // Priority 3: Environment variable (server-side)
  return process.env.QUIX_AUTH_TOKEN || null
}

export function setAuthToken(token: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem('auth_token', token)
  }
}

export function clearAuthToken(): void {
  if (typeof window !== 'undefined') {
    localStorage.removeItem('auth_token')
  }
}

export function isAuthenticated(): boolean {
  return !!getAuthToken()
}
```

### Auth Context (Optional)

```tsx
// lib/contexts/auth-context.tsx

import { createContext, useContext, useState, useEffect } from 'react'
import { getAuthToken, setAuthToken, clearAuthToken } from '@/lib/utils/auth'

interface AuthContextType {
  token: string | null
  isAuthenticated: boolean
  login: (token: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null)

  useEffect(() => {
    setTokenState(getAuthToken())
  }, [])

  const login = (newToken: string) => {
    setAuthToken(newToken)
    setTokenState(newToken)
  }

  const logout = () => {
    clearAuthToken()
    setTokenState(null)
  }

  return (
    <AuthContext.Provider
      value={{
        token,
        isAuthenticated: !!token,
        login,
        logout
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
```

---

## Usage Patterns

### Pattern 1: Simple Data Fetching

```tsx
'use client'

import { useState, useEffect } from 'react'
import { testsApi } from '@/lib/api/tests'
import type { Test } from '@/types'

export function TestsList() {
  const [tests, setTests] = useState<Test[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchTests() {
      try {
        const result = await testsApi.list()
        setTests(result.items)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    }

    fetchTests()
  }, [])

  if (loading) return <div>Loading...</div>
  if (error) return <div>Error: {error}</div>

  return (
    <div>
      {tests.map(test => (
        <div key={test.test_id}>{test.title}</div>
      ))}
    </div>
  )
}
```

### Pattern 2: Custom Hook for Reusability

```tsx
// lib/hooks/use-tests.ts

import { useState, useEffect } from 'react'
import { testsApi } from '@/lib/api/tests'
import type { Test, TestListParams } from '@/types'

export function useTests(params?: TestListParams) {
  const [tests, setTests] = useState<Test[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [totalCount, setTotalCount] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function fetchTests() {
      setLoading(true)
      setError(null)

      try {
        const result = await testsApi.list(params)
        if (!cancelled) {
          setTests(result.items)
          setTotalCount(result.total)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error('Unknown error'))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchTests()

    // Cleanup function to prevent state updates after unmount
    return () => {
      cancelled = true
    }
  }, [JSON.stringify(params)])  // Re-fetch when params change

  return { tests, loading, error, totalCount }
}

// Usage in component
export function TestsList() {
  const [filters, setFilters] = useState({ status: 'draft' })
  const { tests, loading, error, totalCount } = useTests(filters)

  // ...
}
```

### Pattern 3: URL-Based State with Filters

```tsx
'use client'

import { useSearchParams, useRouter } from 'next/navigation'
import { useTests } from '@/lib/hooks/use-tests'

export function TestsListPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // Parse filters from URL
  const filters = {
    status: searchParams.get('status') || undefined,
    tec_id: searchParams.get('tec_id') || undefined,
    q: searchParams.get('q') || undefined,
    page: parseInt(searchParams.get('page') || '1'),
    pageSize: 20
  }

  const { tests, loading, totalCount } = useTests(filters)

  // Update URL when filters change
  const updateFilter = (key: string, value: string | null) => {
    const newParams = new URLSearchParams(searchParams.toString())

    if (value) {
      newParams.set(key, value)
    } else {
      newParams.delete(key)
    }

    // Reset to page 1 when filters change
    if (key !== 'page') {
      newParams.set('page', '1')
    }

    router.push(`/tests?${newParams.toString()}`)
  }

  return (
    <div>
      <TestsFilters filters={filters} onFilterChange={updateFilter} />
      <TestsTable tests={tests} loading={loading} />
      <Pagination
        page={filters.page}
        totalCount={totalCount}
        pageSize={filters.pageSize}
        onPageChange={(page) => updateFilter('page', page.toString())}
      />
    </div>
  )
}
```

### Pattern 4: Mutations with Optimistic Updates

```tsx
import { useState } from 'react'
import { testsApi } from '@/lib/api/tests'
import { useToast } from '@/hooks/use-toast'

export function TestDetail({ test }: { test: Test }) {
  const [localTest, setLocalTest] = useState(test)
  const { toast } = useToast()

  const handleDelete = async () => {
    // Optimistic update
    const previousTest = localTest
    setLocalTest({ ...localTest, _deleted: true })

    try {
      await testsApi.delete(test.test_id)

      toast({ title: 'Test deleted successfully' })
      router.push('/tests')

    } catch (error) {
      // Rollback on error
      setLocalTest(previousTest)

      toast({
        title: 'Failed to delete test',
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: 'destructive'
      })
    }
  }

  return (
    <div>
      {/* ... */}
      <Button onClick={handleDelete}>Delete</Button>
    </div>
  )
}
```

---

## Best Practices

### 1. Always Type API Responses

```tsx
// ❌ Bad: No types
const tests = await apiGet('/tests')

// ✅ Good: Explicit types
const tests = await apiGet<PaginatedResponse<Test>>('/tests')
```

### 2. Handle Loading and Error States

```tsx
// ✅ Good: Complete state handling
export function TestsList() {
  const { tests, loading, error } = useTests()

  if (loading) return <LoadingSkeleton />
  if (error) return <ErrorMessage error={error} />
  if (tests.length === 0) return <EmptyState />

  return <TestsTable tests={tests} />
}
```

### 3. Use Custom Hooks for Data Fetching

```tsx
// ❌ Bad: Duplicate fetch logic in every component
export function Component1() {
  const [tests, setTests] = useState([])
  useEffect(() => {
    testsApi.list().then(setTests)
  }, [])
}

// ✅ Good: Reusable hook
export function Component1() {
  const { tests } = useTests()
}
```

### 4. Centralize API Calls in Service Modules

```tsx
// ❌ Bad: Direct API calls in components
const response = await apiGet('/tests')

// ✅ Good: Use service module
const tests = await testsApi.list()
```

### 5. Use URL State for Filters

```tsx
// ✅ Good: Filters in URL (shareable, bookmarkable)
const status = searchParams.get('status')
const { tests } = useTests({ status })
```

### 6. Cancel Requests on Unmount

```tsx
// ✅ Good: Cleanup to prevent memory leaks
useEffect(() => {
  let cancelled = false

  async function fetch() {
    const data = await api.get()
    if (!cancelled) {
      setState(data)
    }
  }

  fetch()

  return () => {
    cancelled = true
  }
}, [])
```

### 7. Show User Feedback for All Actions

```tsx
// ✅ Good: Toast notifications for success/error
try {
  await testsApi.create(data)
  toast({ title: 'Test created successfully' })
} catch (error) {
  toast({ title: 'Failed to create test', variant: 'destructive' })
}
```

### 8. Validate Before Submitting

```tsx
// ✅ Good: Client-side validation with Zod
const schema = z.object({
  title: z.string().min(1, 'Title is required')
})

const form = useForm({
  resolver: zodResolver(schema)
})

// Then handle server validation errors
catch (error) {
  if (error instanceof ApiError && error.isValidationError()) {
    const errors = error.getValidationErrors()
    // Set form errors
  }
}
```

---

## Testing API Calls (Optional)

### Mock API Client for Tests

```typescript
// lib/api/__mocks__/client.ts

export const apiGet = jest.fn()
export const apiPost = jest.fn()
export const apiPut = jest.fn()
export const apiDelete = jest.fn()
```

### Example Test

```typescript
import { render, screen, waitFor } from '@testing-library/react'
import { testsApi } from '@/lib/api/tests'
import { TestsList } from '@/components/tests/tests-list'

jest.mock('@/lib/api/tests')

test('displays tests', async () => {
  const mockTests = [
    { test_id: '1', title: 'Test 1', status: 'draft' }
  ]

  ;(testsApi.list as jest.Mock).mockResolvedValue({
    items: mockTests,
    total: 1
  })

  render(<TestsList />)

  await waitFor(() => {
    expect(screen.getByText('Test 1')).toBeInTheDocument()
  })
})
```

---

## Summary

This API client architecture provides:

- ✅ **Type Safety**: Full TypeScript typing for all API calls
- ✅ **Error Handling**: Centralized error handling with custom error classes
- ✅ **Authentication**: Automatic token injection
- ✅ **Reusability**: Service modules and custom hooks
- ✅ **Maintainability**: Clean separation of concerns
- ✅ **Developer Experience**: Clear patterns and best practices

---

**Document Version**: 1.0
**Last Updated**: 2025-10-23
**Related**: MIGRATION_PLAN.md, COMPONENT_MAPPING.md
