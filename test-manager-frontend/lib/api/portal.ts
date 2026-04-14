/**
 * API client for Portal operations
 * Provides methods to interact with /portal endpoints
 */

import { apiGet } from "./client"
import type {
  Repository,
  WorkspaceDetails,
  DeploymentInfo,
  TopicInfo,
} from "../types/portal"

export const portalApi = {
  /**
   * Get available repositories (projects) from Portal API
   */
  getRepositories: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Repository[]>("/portal/repositories", undefined, token, refreshToken)
  },

  /**
   * Get workspaces/environments with details
   * @param repositoryId Optional filter by repository/project
   */
  getWorkspaces: (
    repositoryId?: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    const params = repositoryId ? { repository_id: repositoryId } : undefined
    return apiGet<WorkspaceDetails[]>("/portal/workspaces", params, token, refreshToken)
  },

  /**
   * Get deployments for a specific workspace
   * @param workspaceId The workspace ID to get deployments for
   */
  getDeployments: (
    workspaceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<DeploymentInfo[]>(
      `/portal/workspaces/${workspaceId}/deployments`,
      undefined,
      token,
      refreshToken
    )
  },

  /**
   * Get the current workspace ID
   */
  getCurrentWorkspaceId: (
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<{ workspace_id: string }>(
      "/portal/current-workspace-id",
      undefined,
      token,
      refreshToken
    )
  },

  /**
   * Search for a fallback deployment by name in the current workspace
   * @param deploymentName Name to search for (partial match)
   */
  getFallbackDeployment: (
    deploymentName: string = "Dynamic Configuration Manager",
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<DeploymentInfo | null>(
      "/portal/fallback-deployment",
      { deployment_name: deploymentName },
      token,
      refreshToken
    )
  },

  /**
   * Get topics for a specific workspace
   * @param workspaceId The workspace ID to get topics for
   */
  getTopics: (
    workspaceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<TopicInfo[]>(
      `/portal/workspaces/${workspaceId}/topics`,
      undefined,
      token,
      refreshToken
    )
  },
}
