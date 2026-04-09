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

export const TestStatusLabels: Record<TestStatus, string> = {
  [TestStatus.DRAFT]: "Draft",
  [TestStatus.IN_PROGRESS]: "In Progress",
  [TestStatus.FINISHED]: "Finished",
}

export interface DeviceReference {
  device_id: string
  device_version: string | null
}

export interface File {
  id: string
  name: string
  url: string
  size: number
  uploaded_at: string
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
  test_id: string
  experiment_id: string
  pc_device_id: string
  test_rig_device_id: string
  environment_id: string
  driver: string
  requirements: string
  created_at: string
  updated_at: string
  config_id: string
  config_type: string | null
  target_key: string | null
  config_version: number | null
  links: Link[]
  files: Record<string, File>
  status: TestStatus
  start: string | null
  end: string | null
}

export interface TestCreate {
  experiment_id: string
  pc_device_id: string
  test_rig_device_id: string
  environment_id: string
  driver: string
  requirements?: string
  status?: TestStatus
  start?: string | null
  end?: string | null
}

export interface TestUpdate {
  experiment_id?: string
  pc_device_id?: string
  test_rig_device_id?: string
  environment_id?: string
  driver?: string
  requirements?: string
  status?: TestStatus
  start?: string | null
  end?: string | null
}

export interface TestQuery extends PaginationParams {
  experiment_id?: string
  environment_id?: string
  driver?: string
  status?: TestStatus
  q?: string
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
  created_at: string
  timestamp: string
  operator: string
  content: string
  sensor_ids: string[]
}

export interface LogbookEntryCreate {
  operator: string
  content: string
  sensor_ids?: string[]
  timestamp?: string
}

export interface LogbookEntryUpdate {
  operator?: string
  content?: string
  sensor_ids?: string[]
  timestamp?: string
}
