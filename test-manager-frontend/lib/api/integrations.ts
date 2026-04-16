/**
 * API client for Integrations
 * Provides methods to interact with /integrations endpoints
 *
 * @internal - Do not import directly. Use the `useIntegrationsApi()` hook instead:
 * ```typescript
 * import { useIntegrationsApi } from "@/lib/hooks/use-api"
 *
 * const integrationsApi = useIntegrationsApi()
 * const url = await integrationsApi.getConfigManagerUrl(testId)
 * ```
 */

import { apiGet } from "./client";

export interface ConfigManagerUrl {
  url: string;
}

export const integrationsApi = {
  /**
   * Get Portal-embedded URL for Configuration Manager
   * @param streamId - Optional test ID for context-aware filtering
   */
  getConfigManagerUrl: (
    streamId?: string | null,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params = streamId ? { stream_id: streamId } : undefined;
    return apiGet<ConfigManagerUrl>(
      "/integrations/config-manager-url",
      params,
      token,
      refreshToken,
    );
  },

  /**
   * Get direct frontend URL for Configuration Manager (for iframe embedding)
   * @param configId - Optional config ID for context-aware filtering
   * @param configVersion - Optional config version
   */
  getConfigManagerFrontendUrl: (
    configId?: string | null,
    configVersion?: number | null,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params: { config_id?: string; config_version?: number } = {};
    if (configId) params.config_id = configId;
    if (configVersion !== null && configVersion !== undefined) {
      params.config_version = configVersion;
    }
    const queryParams = Object.keys(params).length > 0 ? params : undefined;
    return apiGet<ConfigManagerUrl>(
      "/integrations/config-manager-frontend-url",
      queryParams,
      token,
      refreshToken,
    );
  },

  /**
   * Get Data Lake Explorer URL
   * @param testId - Optional test ID for filtering
   */
  getDataLakeUrl: (
    testId?: string | null,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params = testId ? { test_id: testId } : undefined;
    return apiGet<ConfigManagerUrl>(
      "/integrations/data-lake-url",
      params,
      token,
      refreshToken,
    );
  },

  /**
   * Get Measurements/Query Builder URL
   * @param testId - Test ID for SQL filter
   * @param campaignId - Campaign ID for SQL filter
   * @param environmentId - Environment ID for SQL filter
   */
  getMeasurementsUrl: (
    testId?: string | null,
    campaignId?: string | null,
    environmentId?: string | null,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params: {
      test_id?: string;
      campaign_id?: string;
      environment_id?: string;
    } = {};
    if (testId) params.test_id = testId;
    if (campaignId) params.campaign_id = campaignId;
    if (environmentId) params.environment_id = environmentId;
    const queryParams = Object.keys(params).length > 0 ? params : undefined;
    return apiGet<ConfigManagerUrl>(
      "/integrations/measurements-url",
      queryParams,
      token,
      refreshToken,
    );
  },

  /**
   * Get Analytics/Notebook URL
   * @param testId - Test ID for context
   * @param campaignId - Campaign ID for context
   * @param environmentId - Environment ID for context
   */
  getAnalyticsUrl: (
    testId?: string | null,
    campaignId?: string | null,
    environmentId?: string | null,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params: {
      test_id?: string;
      campaign_id?: string;
      environment_id?: string;
    } = {};
    if (testId) params.test_id = testId;
    if (campaignId) params.campaign_id = campaignId;
    if (environmentId) params.environment_id = environmentId;
    const queryParams = Object.keys(params).length > 0 ? params : undefined;
    return apiGet<ConfigManagerUrl>(
      "/integrations/analytics-url",
      queryParams,
      token,
      refreshToken,
    );
  },

  /**
   * Download test measurement data from DataLake
   * Returns CSV text directly from Quix Lake Query API
   * @param testId - Test ID for filtering
   * @param campaignId - Campaign ID for filtering
   * @param environmentId - Environment ID for filtering
   */
  downloadTestData: (
    testId?: string | null,
    campaignId?: string | null,
    environmentId?: string | null,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params: {
      test_id?: string;
      campaign_id?: string;
      environment_id?: string;
    } = {};
    if (testId) params.test_id = testId;
    if (campaignId) params.campaign_id = campaignId;
    if (environmentId) params.environment_id = environmentId;
    const queryParams = Object.keys(params).length > 0 ? params : undefined;
    return apiGet<string>(
      "/integrations/download-test-data",
      queryParams,
      token,
      refreshToken,
    );
  },
};
