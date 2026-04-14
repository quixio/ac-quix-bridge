/**
 * TypeScript types for Device entity
 * Mirrors backend/api/models.py Device models
 */

import type { PaginationParams } from "./pagination"

export enum DeviceStatus {
  ACTIVE = "active",
  INACTIVE = "inactive",
}

export const DeviceStatusLabels: Record<DeviceStatus, string> = {
  [DeviceStatus.ACTIVE]: "Active",
  [DeviceStatus.INACTIVE]: "Inactive",
}

export enum DeviceCategory {
  PC = "pc",
  TEST_RIG = "test_rig",
}

export const DeviceCategoryLabels: Record<DeviceCategory, string> = {
  [DeviceCategory.PC]: "PC",
  [DeviceCategory.TEST_RIG]: "Test Rig",
}

export interface Device {
  device_id: string
  category: DeviceCategory
  name: string
  status: DeviceStatus
  created_at: string
  updated_at: string
}

export interface DeviceCreate {
  category: DeviceCategory
  name: string
  status?: DeviceStatus
}

export interface DeviceUpdate {
  name?: string
  category?: DeviceCategory
  status?: DeviceStatus
}

export interface DeviceQuery extends PaginationParams {
  category?: DeviceCategory
  status?: DeviceStatus
  q?: string
}
