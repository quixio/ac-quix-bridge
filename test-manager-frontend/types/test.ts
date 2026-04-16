/**
 * TypeScript types for Test entity
 * Mirrors backend/api/models.py Test models
 */

import type { PaginationParams } from "./pagination";

export interface DeviceReference {
  device_id: string;
  device_version: string | null;
}

export interface File {
  id: string;
  name: string;
  url: string;
  size: number;
  uploaded_at: string;
}

export interface Link {
  id: string;
  url: string;
  label: string;
}

export interface LinkCreate {
  url: string;
  label: string;
}

export interface SessionInfo {
  session_id: string;
  track: string;
  car_model: string;
}

export interface Test {
  test_id: string;
  experiment_id: string;
  pc_device_id: string;
  test_rig_device_id: string;
  environment_id: string;
  driver: string;
  requirements: string;
  sessions: SessionInfo[];
  // Resolved display names from backend
  pc_device_name: string | null;
  test_rig_device_name: string | null;
  environment_name: string | null;
  created_at: string;
  updated_at: string;
  config_id: string;
  config_type: string | null;
  target_key: string | null;
  config_version: number | null;
}

export interface TestCreate {
  experiment_id: string;
  pc_device_id: string;
  test_rig_device_id: string;
  environment_id: string;
  driver: string;
  requirements?: string;
}

export interface TestUpdate {
  experiment_id?: string;
  pc_device_id?: string;
  test_rig_device_id?: string;
  environment_id?: string;
  driver?: string;
  requirements?: string;
}

export interface TestQuery extends PaginationParams {
  experiment_id?: string;
  environment_id?: string;
  driver?: string;
  q?: string;
}

export interface TestFullData {
  test: Test;
  files: File[];
  logbook: LogbookEntry[];
  links: Link[];
}

export interface LogbookEntry {
  id: string;
  test_id: string;
  created_at: string;
  content: string;
}

export interface LogbookEntryCreate {
  content: string;
}

export interface LogbookEntryUpdate {
  content?: string;
}
