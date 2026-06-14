/**
 * TypeScript types for Driver entity
 * Mirrors backend/api/models.py Driver models
 */

import type { PaginationParams } from "./pagination";

export interface Driver {
  driver_id: string;
  name: string;
  // Optional on the read model: drivers created before these fields existed.
  email: string | null;
  company: string | null;
  created_at: string;
  updated_at: string;
}

export interface DriverCreate {
  name: string;
  email: string;
  company: string;
}

// Name is the lake identity and is locked after create — not updatable.
export interface DriverUpdate {
  email?: string;
  company?: string;
}

export interface DriverQuery extends PaginationParams {
  name?: string;
  q?: string;
}
