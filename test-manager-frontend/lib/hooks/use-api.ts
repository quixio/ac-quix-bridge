"use client";

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

import { useMemo } from "react";
import { useQuixAuth } from "../contexts/quix-auth-context";
import { ApiError } from "../api/client";
import { devicesApi as devicesApiRaw } from "../api/devices";
import { testsApi as testsApiRaw } from "../api/tests";
import { logbookApi as logbookApiRaw } from "../api/logbook";
import { integrationsApi as integrationsApiRaw } from "../api/integrations";
import { settingsApi as settingsApiRaw } from "../api/settings";
import { portalApi as portalApiRaw } from "../api/portal";
import { driversApi as driversApiRaw } from "../api/drivers";
import { environmentsApi as environmentsApiRaw } from "../api/environments";
import { leaderboardApi as leaderboardApiRaw } from "../api/leaderboard";

/**
 * Generic helper to create an authenticated API client hook
 */
function createAuthenticatedApi<
  T extends Record<string, (...args: any[]) => any>,
>(api: T) {
  return function useAuthenticatedApiHook() {
    const { token, refreshToken, clearTokenAndPrompt, isEmbedded, isLoading } =
      useQuixAuth();

    // Memoize the authenticated API object to prevent infinite re-renders
    // Only recreate when token or refreshToken changes
    const authenticatedApi = useMemo(() => {
      const apiObj = {} as {
        [K in keyof T]: (
          ...args: Parameters<T[K]> extends [...infer P, any, any]
            ? P
            : Parameters<T[K]>
        ) => ReturnType<T[K]>;
      };

      for (const key in api) {
        const originalFn = api[key];
        // @ts-ignore - Dynamic function wrapping
        apiObj[key] = async (...args: any[]) => {
          // MainLayout gates rendering on auth-ready, so by the time any
          // component fires an API call, the token is already in place.
          try {
            return await originalFn(...args, token, refreshToken);
          } catch (error) {
            // Only prompt for a new token in standalone mode AND after the auth
            // context has finished initializing. This avoids a race during embedded
            // mount where isEmbedded is still false from its initial useRef value.
            if (
              !isLoading &&
              !isEmbedded &&
              error instanceof ApiError &&
              (error.status === 401 || error.status === 403)
            ) {
              clearTokenAndPrompt();
            }
            throw error;
          }
        };
      }

      return apiObj;
    }, [token, refreshToken, clearTokenAndPrompt, isEmbedded, isLoading]);

    return authenticatedApi;
  };
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
export const useDevicesApi = createAuthenticatedApi(devicesApiRaw);

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
export const useTestsApi = createAuthenticatedApi(testsApiRaw);

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
export const useLogbookApi = createAuthenticatedApi(logbookApiRaw);

/**
 * Authenticated Integrations API Hook
 *
 * @example
 * ```typescript
 * const integrationsApi = useIntegrationsApi()
 * const { url } = await integrationsApi.getConfigManagerUrl("test-123")
 * ```
 */
export const useIntegrationsApi = createAuthenticatedApi(integrationsApiRaw);

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
export const useSettingsApi = createAuthenticatedApi(settingsApiRaw);

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
export const usePortalApi = createAuthenticatedApi(portalApiRaw);

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
export const useDriversApi = createAuthenticatedApi(driversApiRaw);

/**
 * Authenticated Environments API Hook
 */
export const useEnvironmentsApi = createAuthenticatedApi(environmentsApiRaw);

/**
 * Authenticated Leaderboard API Hook (multi-driver live positions).
 *
 * @example
 * ```typescript
 * const leaderboardApi = useLeaderboardApi()
 * const rows = await leaderboardApi.getLivePositions()
 * ```
 */
export const useLeaderboardApi = createAuthenticatedApi(leaderboardApiRaw);

/**
 * Stub for the unmerged AI-analysis hook. The ai-summary tab imports this,
 * but its underlying API client wasn't carried over when the leaderboard
 * rebuild was integrated. The returned proxy rejects every method call so
 * the AI Summary tab surfaces a clear runtime error when opened, while the
 * rest of the app compiles and runs normally.
 *
 * TODO: replace with the real hook once the post-race-AI feature is merged.
 */
// eslint-disable-next-line
type StubMethod = (...args: never[]) => Promise<{ items?: unknown[]; [k: string]: unknown }>;
type StubAPI = Record<string, StubMethod>;
export const useAnalysesApi = (): StubAPI =>
  new Proxy(
    {} as StubAPI,
    {
      get:
        () =>
        (..._args: never[]) =>
          Promise.reject(
            new Error(
              "useAnalysesApi is not implemented on this branch — post-race-AI analyzer hook missing.",
            ),
          ),
    },
  );
