/**
 * TypeScript types for Driver entity
 * Mirrors backend/api/models.py Driver models
 */

import type { PaginationParams } from "./pagination"

export interface Driver {
  driver_id: string
  name: string
  created_at: string
  updated_at: string
}

export interface DriverCreate {
  name: string
}

export interface DriverUpdate {
  name?: string
}

export interface DriverQuery extends PaginationParams {
  name?: string
  q?: string
}
