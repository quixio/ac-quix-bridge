/**
 * TypeScript types for Device (Device Under Test) entity
 * Mirrors backend/api/models.py Device models
 */

import type { PaginationParams } from "./pagination"

export enum DeviceStatus {
  CREATED = "created",
  SETUP = "setup",
  STORED = "stored",
  SCRAPPED = "scrapped",
}

export const DeviceStatusLabels: Record<DeviceStatus, string> = {
  [DeviceStatus.CREATED]: "Created",
  [DeviceStatus.SETUP]: "Setup",
  [DeviceStatus.STORED]: "Stored",
  [DeviceStatus.SCRAPPED]: "Scrapped",
}

export const DeviceStatusColors: Record<DeviceStatus, string> = {
  [DeviceStatus.CREATED]: "blue",
  [DeviceStatus.SETUP]: "yellow",
  [DeviceStatus.STORED]: "green",
  [DeviceStatus.SCRAPPED]: "red",
}

export enum JournalCategory {
  SAFETY_REQUIREMENTS = "Safety Requirements",
  SETUP = "Setup",
  TESTING = "Testing",
  CHANGE_LOCATION = "Change-Location",
  HW_MODIFICATION = "HW Modification",
  SW_MODIFICATION = "SW Modification",
}

export interface Device {
  device_id: string // Maps to _id in backend
  status: DeviceStatus
  status_note: string | null
  created_at: string // ISO datetime string
  updated_at: string // ISO datetime string
  creator: string
  last_editor: string

  // Product fields (strings from lookups)
  manufacturer: string
  product_category: string
  product_name: string
  product_type: string | null
  product_variant: string | null
  product_key: string | null

  // Sample fields
  sample_type: string
  sample_nr: string | null
  sample_id: string // Derived: {sample_type} or {sample_type}-{sample_nr}
  teamcenter_serial_nr: string | null

  // Organization info
  sample_owner: string | null
  location: string
  project: string | null
  picture_link: string | null

  // Misc metadata
  software_bundle: string | null
  hardware_link: string | null
  comment: string | null
  attended_operation: boolean // Calculated from safety requirements
  unattended_operation: boolean // Calculated from safety requirements
}

export interface DeviceCreate {
  device_id: string
  manufacturer: string
  product_category: string
  product_name: string
  product_type?: string | null
  product_variant?: string | null
  product_key?: string | null
  sample_type: string
  sample_nr?: string | null
  location: string
  status?: DeviceStatus
  status_note?: string | null
  teamcenter_serial_nr?: string | null
  sample_owner?: string | null
  project?: string | null
  picture_link?: string | null
  software_bundle?: string | null
  hardware_link?: string | null
  comment?: string | null
  creator: string
  journal_text?: string | null
  journal_category?: JournalCategory | null
}

export interface DeviceUpdate {
  // Note: Some dropdowns (Manufacturer, Product Category, Product Name, Product Type) will be available in Phase 2.2

  // Product fields
  manufacturer?: string | null
  product_category?: string | null
  product_name?: string | null
  product_type?: string | null
  product_variant?: string | null
  product_key?: string | null

  // Sample fields (all editable)
  sample_type?: string | null
  sample_nr?: string | null
  teamcenter_serial_nr?: string | null

  // Status
  status?: DeviceStatus | null
  status_note?: string | null

  // Organization info
  location?: string | null
  project?: string | null
  sample_owner?: string | null
  picture_link?: string | null

  // Misc metadata
  software_bundle?: string | null
  hardware_link?: string | null
  comment?: string | null

  // Audit
  last_editor?: string | null

  // Journal metadata (not stored on device, used for journal entry creation)
  journal_text?: string | null
  journal_category?: JournalCategory | null
}

export interface DeviceQuery extends PaginationParams {
  device_id?: string
  status?: DeviceStatus
  manufacturer?: string
  product_category?: string
  product_name?: string
  location?: string
  project?: string
  sample_type?: string
  sample_id?: string
  creator?: string
  q?: string // Text search across multiple fields
  id_search?: string // Quick search by Device ID or Sample ID only
}

export interface DeviceJournalEntry {
  _id: string // Journal entry id (aka device_version)
  device_id: string
  timestamp: string // ISO datetime string
  editor: string
  category: JournalCategory | null
  text: string
  data: Device // Full JSON snapshot of the Device at this time
}

export interface DeviceJournalEntryCreate {
  editor: string
  category?: JournalCategory | null
  text: string
}

export interface DeviceUpdatePreview {
  suggested_text: string
  changed_fields: string[]
}

// Lookup types
export interface SampleType {
  _id: string
  sample_type: string
}

export interface Location {
  _id: string
  location: string
}

export interface Product {
  _id: string
  manufacturer: string
  product_category: string
  product_name: string
}

export interface ProductCategory {
  _id: string // Product category key (e.g., "WP")
  name: string // Human-readable name (e.g., "Heat Pump")
}
