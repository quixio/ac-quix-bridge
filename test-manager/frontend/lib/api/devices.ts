/**
 * API client for Devices (Device Under Test)
 *
 * @internal - Do not import directly. Use the `useDevicesApi()` hook instead:
 * ```typescript
 * import { useDevicesApi } from "@/lib/hooks/use-api"
 *
 * const devicesApi = useDevicesApi()
 * const devices = await devicesApi.list(params) // Auth auto-injected
 * ```
 */

import { apiGet, apiPost, apiPut, apiDelete } from "./client"
import type {
  Device,
  DeviceCreate,
  DeviceUpdate,
  DeviceQuery,
  DeviceJournalEntry,
  DeviceJournalEntryCreate,
  DeviceUpdatePreview,
} from "@/types/device"
import type { PaginatedResponse } from "@/types/pagination"

export const devicesApi = {
  /**
   * List all devices with optional filtering and pagination
   */
  list: (
    params?: DeviceQuery,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<PaginatedResponse<Device>>("/devices", params, token, refreshToken)
  },

  /**
   * Get a single device by ID
   */
  get: (
    deviceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<Device>(`/devices/${deviceId}`, undefined, token, refreshToken)
  },

  /**
   * Get multiple devices in a single request (batch)
   * Optimizes performance by eliminating N+1 query pattern
   */
  getBatch: (
    deviceIds: string[],
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<Device[]>("/devices/batch", deviceIds, token, refreshToken)
  },

  /**
   * Create a new device
   */
  create: (
    data: DeviceCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<Device>("/devices", data, token, refreshToken)
  },

  /**
   * Update an existing device
   */
  update: (
    deviceId: string,
    data: DeviceUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPut<Device>(`/devices/${deviceId}`, data, token, refreshToken)
  },

  /**
   * Delete a device
   */
  delete: (
    deviceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiDelete(`/devices/${deviceId}`, token, refreshToken)
  },

  /**
   * Get journal history for a device
   */
  getJournal: (
    deviceId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiGet<DeviceJournalEntry[]>(`/devices/${deviceId}/journal`, undefined, token, refreshToken)
  },

  /**
   * Create a manual journal entry for a device
   */
  createJournalEntry: (
    deviceId: string,
    data: DeviceJournalEntryCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<DeviceJournalEntry>(`/devices/${deviceId}/journal`, data, token, refreshToken)
  },

  /**
   * Preview device changes before saving (returns suggested text and changed fields)
   */
  preview: (
    deviceId: string,
    data: DeviceUpdate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>
  ) => {
    return apiPost<DeviceUpdatePreview>(`/devices/${deviceId}/preview-update`, data, token, refreshToken)
  },
}
