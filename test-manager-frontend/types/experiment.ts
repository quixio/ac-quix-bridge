/**
 * TypeScript types for Experiment entity
 * Mirrors backend/api/models.py Experiment models
 */

import type { PaginationParams } from "./pagination";

export interface Experiment {
  experiment_id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface ExperimentCreate {
  name: string;
}

export interface ExperimentQuery extends PaginationParams {
  name?: string;
  q?: string;
}
