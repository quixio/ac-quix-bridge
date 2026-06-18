/**
 * TypeScript types for Test entity
 * Mirrors backend/api/models.py Test models
 */

import type { PaginationParams } from "./pagination";

export type TestMode = "easy" | "medium" | "pro";

export interface DeviceReference {
  device_id: string;
  device_version: string | null;
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
  mode: TestMode | null;
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

// Most recent test's values, for prefilling the create-test form.
export interface LastUsedDefaults {
  pc_device_id: string | null;
  test_rig_device_id: string | null;
  environment_id: string | null;
  driver: string | null;
  experiment_id: string | null;
  mode: TestMode | null;
  requirements: string;
}

export interface TestCreate {
  experiment_id: string;
  pc_device_id: string;
  test_rig_device_id: string;
  environment_id: string;
  driver: string;
  requirements?: string;
  mode: TestMode;
}

export interface TestUpdate {
  experiment_id?: string;
  pc_device_id?: string;
  test_rig_device_id?: string;
  environment_id?: string;
  driver?: string;
  requirements?: string;
  mode?: TestMode;
}

export interface TestQuery extends PaginationParams {
  experiment_id?: string;
  environment_id?: string;
  driver?: string;
  q?: string;
}

export interface TestFullData {
  test: Test;
  logbook: LogbookEntry[];
}

export interface LogbookEntry {
  id: string;
  test_id: string;
  session_id: string | null;
  created_at: string;
  content: string;
}

export interface LogbookEntryCreate {
  content: string;
  session_id?: string | null;
}

export interface LogbookEntryUpdate {
  content?: string;
  session_id?: string | null;
}
