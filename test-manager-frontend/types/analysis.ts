export type AnalysisStatus =
  | "pending"
  | "running"
  | "fetching"
  | "analyzing"
  | "saving"
  | "complete"
  | "failed";

export type ErrorKind = "timeout" | "agent" | "validation" | "orphan";

export interface KpiValue {
  name: string;
  value: number | string;
  unit?: string | null;
  notes?: string | null;
  session_id?: string | null;
}

export interface RequirementCheck {
  requirement: string;
  met?: boolean | null;
  evidence?: string | null;
}

export interface Anomaly {
  severity: "info" | "warn" | "error";
  kind: string;
  lap?: number | null;
  time_ms?: number | null;
  description: string;
  evidence?: string | null;
  session_id?: string | null;
}

export interface Analysis {
  id: string;
  schema_version: number;
  test_id: string;
  session_id: string | null;
  status: AnalysisStatus;
  created_at: string;
  updated_at: string;
  quix_session_id?: string | null;
  model?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  tokens_cache_create?: number | null;
  tokens_cache_read?: number | null;
  duration_ms?: number | null;
  error?: string | null;
  error_kind?: ErrorKind | null;
  kpis: KpiValue[];
  requirements_check: RequirementCheck[];
  logbook_refs: string[];
  anomalies: Anomaly[];
  summary_md: string;
  extra: Record<string, unknown>;
}

export interface AnalysisCreateRequest {
  test_id: string;
  session_id: string | null;
}

export interface AnalysisListResponse {
  items: Analysis[];
  total: number;
  page: number;
  page_size: number;
}
