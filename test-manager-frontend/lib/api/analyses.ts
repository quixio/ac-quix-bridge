/**
 * API client for AI Analyses
 * Provides methods to interact with /analyses endpoints
 */

import { apiGet, apiPost } from "./client";
import type {
  Analysis,
  AnalysisCreateRequest,
  AnalysisListResponse,
} from "@/types/analysis";

export const analysesApi = {
  /**
   * Create a new analysis (kicks off the runner; returns analysis_id)
   */
  create: (
    data: AnalysisCreateRequest,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<{ analysis_id: string }>(
      `/analyses`,
      data,
      token,
      refreshToken,
    );
  },

  /**
   * Get a single analysis by id
   */
  get: (
    analysisId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<Analysis>(
      `/analyses/${analysisId}`,
      undefined,
      token,
      refreshToken,
    );
  },

  /**
   * List analyses with optional filters + pagination
   */
  list: (
    opts?: {
      testId?: string;
      sessionId?: string;
      status?: "complete" | "failed" | "in_progress";
      page?: number;
      pageSize?: number;
    },
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    const params: Record<string, string | number> = {};
    if (opts?.testId !== undefined) params.test_id = opts.testId;
    if (opts?.sessionId !== undefined) params.session_id = opts.sessionId;
    if (opts?.status !== undefined) params.status = opts.status;
    if (opts?.page !== undefined) params.page = opts.page;
    if (opts?.pageSize !== undefined) params.page_size = opts.pageSize;
    return apiGet<AnalysisListResponse>(
      `/analyses`,
      Object.keys(params).length > 0 ? params : undefined,
      token,
      refreshToken,
    );
  },
};
