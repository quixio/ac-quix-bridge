/**
 * API client for Settings operations
 * Provides methods to interact with /settings endpoints
 */

import { apiGet, apiPut } from "./client"
import type { DeploymentReference, TopicReference } from "../types/portal"

export interface IntegrationSettings {
  // Configurations - Dynamic Configuration Manager
  config_api_deployment: DeploymentReference | null
  config_api_is_fallback: boolean

  // Measurements - Query UI deployment and topic
  measurements_deployment: DeploymentReference | null
  measurements_topic: TopicReference | null
  measurements_is_fallback: boolean

  // Analytics - Marimo/Analytics deployment
  analytics_deployment: DeploymentReference | null
  analytics_is_fallback: boolean

  updated_at: string | null
  updated_by: string | null
}

export interface IntegrationSettingsUpdate {
  // Config API
  config_api_deployment?: DeploymentReference | null

  // Measurements
  measurements_deployment?: DeploymentReference | null
  measurements_topic?: TopicReference | null

  // Analytics
  analytics_deployment?: DeploymentReference | null
}

export interface Topic {
  id: string
  name: string
}

export interface Workspace {
  id: string
  name: string
}

export const settingsApi = {
  /**
   * Get current integration settings
   */
  getSettings: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<IntegrationSettings>("/settings", undefined, token, refreshToken)
  },

  /**
   * Update integration settings
   */
  updateSettings: (
    data: IntegrationSettingsUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPut<IntegrationSettings>("/settings", data, token, refreshToken)
  },

  /**
   * Get available topics from Portal API
   */
  getTopics: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Topic[]>("/settings/topics", undefined, token, refreshToken)
  },

  /**
   * Get available workspaces from Portal API
   */
  getWorkspaces: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Workspace[]>("/settings/workspaces", undefined, token, refreshToken)
  },
}
