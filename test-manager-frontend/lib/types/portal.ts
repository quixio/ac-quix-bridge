/**
 * TypeScript types for Portal API entities
 * Used for deployment selection and workspace navigation
 */

/**
 * Repository (Project) from Portal API
 * Note: Backend returns camelCase due to Pydantic aliases
 */
export interface Repository {
  repositoryId: string
  name: string
}

/**
 * Workspace with extended details from Portal API
 * Note: Backend returns camelCase due to Pydantic aliases
 */
export interface WorkspaceDetails {
  workspaceId: string
  name: string
  repositoryId: string
  environmentName: string
  status: string
}

/**
 * Deployment information from Portal API
 * Note: Some fields use camelCase (from aliases), others snake_case
 */
export interface DeploymentInfo {
  deploymentId: string
  name: string
  status: string
  publicUrl: string | null
  embedded_view_url: string | null
  service_name: string | null
  publicAccess: boolean
}

/**
 * Reference to a selected deployment stored in settings
 */
export interface DeploymentReference {
  deployment_id: string
  workspace_id: string
  deployment_name: string
  public_url: string | null
  embedded_view_url: string | null
  internal_url: string | null
}

/**
 * Topic information from Portal API with full details
 */
export interface TopicInfo {
  topicId: string
  name: string
  workspaceId: string
  status: string | null
}

/**
 * Reference to a selected topic stored in settings
 */
export interface TopicReference {
  topic_name: string
  workspace_id: string
  workspace_name: string | null
}

/**
 * Tree node structure for the deployment picker
 */
export interface DeploymentTreeNode {
  id: string
  name: string
  type: "repository" | "workspace" | "deployment"
  children?: DeploymentTreeNode[]
  data?: Repository | WorkspaceDetails | DeploymentInfo
  isExpanded?: boolean
  isLoading?: boolean
}

/**
 * Tree node structure for the topic picker
 */
export interface TopicTreeNode {
  id: string
  name: string
  type: "repository" | "workspace" | "topic"
  children?: TopicTreeNode[]
  data?: Repository | WorkspaceDetails | TopicInfo
  isExpanded?: boolean
  isLoading?: boolean
}
