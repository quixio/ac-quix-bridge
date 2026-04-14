/**
 * TypeScript types for Environment entity
 * Mirrors backend/api/models.py Environment models
 */

import type { PaginationParams } from "./pagination"

export enum EnvironmentStatus {
  ACTIVE = "active",
  INACTIVE = "inactive",
}

export const EnvironmentStatusLabels: Record<EnvironmentStatus, string> = {
  [EnvironmentStatus.ACTIVE]: "Active",
  [EnvironmentStatus.INACTIVE]: "Inactive",
}

export const EnvironmentStatusColors: Record<EnvironmentStatus, string> = {
  [EnvironmentStatus.ACTIVE]: "green",
  [EnvironmentStatus.INACTIVE]: "red",
}

export interface Environment {
  environment_id: string
  name: string
  location: string | null
  status: EnvironmentStatus
  created_at: string
  updated_at: string
}

export interface EnvironmentCreate {
  name: string
  location?: string
  status?: EnvironmentStatus
}

export interface EnvironmentUpdate {
  name?: string
  location?: string
  status?: EnvironmentStatus
}

export interface EnvironmentQuery extends PaginationParams {
  name?: string
  location?: string
  status?: EnvironmentStatus
  q?: string
}
