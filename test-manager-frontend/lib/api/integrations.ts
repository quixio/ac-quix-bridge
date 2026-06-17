/**
 * API client for Integrations
 * Provides methods to interact with /integrations endpoints
 *
 * @internal - Do not import directly. Use the `useIntegrationsApi()` hook instead:
 * ```typescript
 * import { useIntegrationsApi } from "@/lib/hooks/use-api"
 *
 * const integrationsApi = useIntegrationsApi()
 * const { url } = await integrationsApi.getConfigManagerFrontendUrl(configId, version)
 * ```
 */

import { apiGet } from "./client";

export interface ConfigManagerUrl {
  url: string;
}

export const integrationsApi = {
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
};
