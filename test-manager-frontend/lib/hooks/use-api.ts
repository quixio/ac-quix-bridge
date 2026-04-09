"use client"

/**
 * Authenticated API Client Hooks - MAIN ENTRY POINT
 *
 * This is the recommended way to use all API clients in this application.
 * These hooks automatically inject authentication tokens and handle token refresh.
 *
 * WHY USE THESE HOOKS:
 * - ✅ Clean, simple API - no manual token passing
 * - ✅ Automatic token refresh on 401/403 errors
 * - ✅ Type-safe with full IntelliSense support
 * - ✅ Consistent pattern across the entire codebase
 *
 * USAGE:
 * ```typescript
 * import { useDacsApi, useTestsApi } from "@/lib/hooks/use-api"
 *
 * function MyComponent() {
 *   const dacsApi = useDacsApi()
 *   const testsApi = useTestsApi()
 *
 *   // Use directly - auth is automatic!
 *   const dacs = await dacsApi.list(params)
 *   const tests = await testsApi.list(query)
 * }
 * ```
 *
 * DO NOT import raw API clients from @/lib/api/* directly.
 * Always use these hooks instead.
 */

import { useMemo } from "react"
import { useQuixAuth } from "../contexts/quix-auth-context"
import { ApiError } from "../api/client"
import { devicesApi as devicesApiRaw } from "../api/devices"
import { testsApi as testsApiRaw } from "../api/tests"
import { lookupsApi as lookupsApiRaw } from "../api/lookups"
import { linksApi as linksApiRaw } from "../api/links"
import { logbookApi as logbookApiRaw } from "../api/logbook"
import { filesApi as filesApiRaw } from "../api/files"
import { adminApi as adminApiRaw } from "../api/admin"
import { integrationsApi as integrationsApiRaw } from "../api/integrations"
import { settingsApi as settingsApiRaw } from "../api/settings"
import { portalApi as portalApiRaw } from "../api/portal"
import { driversApi as driversApiRaw } from "../api/drivers"

/**
 * Generic helper to create an authenticated API client hook
 */
function createAuthenticatedApi<T extends Record<string, (...args: any[]) => any>>(
  api: T
) {
  return function useAuthenticatedApiHook() {
    const { token, refreshToken, clearTokenAndPrompt, isEmbedded } = useQuixAuth()

    // Memoize the authenticated API object to prevent infinite re-renders
    // Only recreate when token or refreshToken changes
    const authenticatedApi = useMemo(() => {
      const apiObj = {} as {
        [K in keyof T]: (...args: Parameters<T[K]> extends [...infer P, any, any] ? P : Parameters<T[K]>) => ReturnType<T[K]>
      }

      for (const key in api) {
        const originalFn = api[key]
        // @ts-ignore - Dynamic function wrapping
        apiObj[key] = async (...args: any[]) => {
          try {
            return await originalFn(...args, token, refreshToken)
          } catch (error) {
            // In standalone mode, if auth fails after retry, prompt for new token
            if (
              !isEmbedded &&
              error instanceof ApiError &&
              (error.status === 401 || error.status === 403)
            ) {
              clearTokenAndPrompt()
            }
            throw error
          }
        }
      }

      return apiObj
    }, [token, refreshToken, clearTokenAndPrompt, isEmbedded])

    return authenticatedApi
  }
}

/**
 * Authenticated Devices API Hook
 *
 * @example
 * ```typescript
 * const devicesApi = useDevicesApi()
 * const devices = await devicesApi.list({ status: "active" })
 * const device = await devicesApi.get("DEV-001")
 * ```
 */
export const useDevicesApi = createAuthenticatedApi(devicesApiRaw)

/**
 * Authenticated Tests API Hook
 *
 * @example
 * ```typescript
 * const testsApi = useTestsApi()
 * const tests = await testsApi.list({ status: "in_progress" })
 * const test = await testsApi.get("test-123")
 * ```
 */
export const useTestsApi = createAuthenticatedApi(testsApiRaw)

/**
 * Authenticated Lookups API Hook
 *
 * @example
 * ```typescript
 * const lookupsApi = useLookupsApi()
 * const sampleTypes = await lookupsApi.getSampleTypes()
 * const locations = await lookupsApi.getLocations()
 * ```
 */
export const useLookupsApi = createAuthenticatedApi(lookupsApiRaw)

/**
 * Authenticated Links API Hook
 *
 * @example
 * ```typescript
 * const linksApi = useLinksApi()
 * const links = await linksApi.list("test-123")
 * await linksApi.create("test-123", { title: "Spec", url: "..." })
 * ```
 */
export const useLinksApi = createAuthenticatedApi(linksApiRaw)

/**
 * Authenticated Logbook API Hook
 *
 * @example
 * ```typescript
 * const logbookApi = useLogbookApi()
 * const entries = await logbookApi.list("test-123")
 * await logbookApi.create("test-123", { content: "Test started" })
 * ```
 */
export const useLogbookApi = createAuthenticatedApi(logbookApiRaw)

/**
 * Authenticated Files API Hook
 *
 * @example
 * ```typescript
 * const filesApi = useFilesApi()
 * const files = await filesApi.list("test-123")
 * const { url } = await filesApi.getPresignedUploadUrl("test-123", "data.csv")
 * ```
 */
export const useFilesApi = createAuthenticatedApi(filesApiRaw)

/**
 * Authenticated Admin API Hook
 *
 * @example
 * ```typescript
 * const adminApi = useAdminApi()
 * await adminApi.seedTestData({ num_dacs: 10, num_tests: 20 })
 * ```
 */
export const useAdminApi = createAuthenticatedApi(adminApiRaw)

/**
 * Authenticated Integrations API Hook
 *
 * @example
 * ```typescript
 * const integrationsApi = useIntegrationsApi()
 * const { url } = await integrationsApi.getConfigManagerUrl("test-123")
 * ```
 */
export const useIntegrationsApi = createAuthenticatedApi(integrationsApiRaw)

/**
 * Authenticated Settings API Hook
 *
 * @example
 * ```typescript
 * const settingsApi = useSettingsApi()
 * const settings = await settingsApi.getSettings()
 * await settingsApi.updateSettings({ measurements_url: "..." })
 * const topics = await settingsApi.getTopics()
 * ```
 */
export const useSettingsApi = createAuthenticatedApi(settingsApiRaw)

/**
 * Authenticated Portal API Hook
 *
 * @example
 * ```typescript
 * const portalApi = usePortalApi()
 * const repositories = await portalApi.getRepositories()
 * const workspaces = await portalApi.getWorkspaces(repositoryId)
 * const deployments = await portalApi.getDeployments(workspaceId)
 * ```
 */
export const usePortalApi = createAuthenticatedApi(portalApiRaw)

/**
 * Authenticated Drivers API Hook
 *
 * @example
 * ```typescript
 * const driversApi = useDriversApi()
 * const drivers = await driversApi.list()
 * await driversApi.create({ name: "Daniel" })
 * ```
 */
export const useDriversApi = createAuthenticatedApi(driversApiRaw)
