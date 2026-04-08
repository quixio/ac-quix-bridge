/**
 * TypeScript types for Test entity
 * Mirrors backend/api/models.py Test models
 */

import type { PaginationParams } from "./pagination"

export enum TestStatus {
  DRAFT = "draft",
  IN_PROGRESS = "in_progress",
  FINISHED = "finished",
}

export interface DeviceReference {
  device_id: string
  device_version: string | null // UUID of DeviceJournalEntry, set when test starts
}

export interface File {
  id: string
  name: string
  url: string
  size: number
  uploaded_at: string // ISO datetime string
}

export interface Link {
  id: string
  url: string
  label: string
}

export interface LinkCreate {
  url: string
  label: string
}

export interface Test {
  test_id: string // Maps to _id in backend
  campaign_id: string
  devices: DeviceReference[] // Required, at least one device
  environment_id: string // Environment identifier
  environment_version: string | null // UUID of EnvironmentJournalEntry, set when test starts
  operator: string
  created_at: string // ISO datetime string
  updated_at: string // ISO datetime string
  sensors: Record<string, Record<string, any>>
  config_id: string
  config_type: string | null // From Dynamic Configuration metadata.type
  target_key: string | null // From Dynamic Configuration metadata.target_key
  config_version: number | null // From Dynamic Configuration metadata.version
  links: Link[]
  files: Record<string, File>
  status: TestStatus
  start: string | null // ISO datetime string
  end: string | null // ISO datetime string
}

export interface TestCreate {
  test_id: string
  campaign_id: string
  devices: DeviceReference[] // Required, at least one device
  environment_id: string
  operator: string
  sensors: Record<string, Record<string, any>>
  status?: TestStatus
  start?: string | null
  end?: string | null
}

export interface TestUpdate {
  campaign_id?: string
  devices?: DeviceReference[]
  environment_id?: string
  operator?: string
  sensors?: Record<string, Record<string, any>>
  status?: TestStatus
  start?: string | null
  end?: string | null
}

export interface TestQuery extends PaginationParams {
  test_id?: string
  campaign_id?: string
  device_id?: string // Filter tests containing this device
  environment_id?: string
  operator?: string
  status?: TestStatus
  q?: string // Text search
}

export interface TestFullData {
  test: Test
  files: File[]
  logbook: LogbookEntry[]
  links: Link[]
}

export interface LogbookEntry {
  id: string
  test_id: string
  created_at: string // ISO datetime string
  timestamp: string // ISO datetime string
  operator: string
  content: string
  sensor_ids: string[]
}

export interface LogbookEntryCreate {
  operator: string
  content: string
  sensor_ids?: string[]
  timestamp?: string // ISO datetime string
}

export interface LogbookEntryUpdate {
  operator?: string
  content?: string
  sensor_ids?: string[]
  timestamp?: string // ISO datetime string
}
