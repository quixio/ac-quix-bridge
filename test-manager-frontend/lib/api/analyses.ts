/**
 * API client for AI Analyses
 * Provides methods to interact with /analyses endpoints
 */

import { apiGet, apiGetBlob, apiPost } from "./client";
import type {
  Analysis,
  AnalysisCreateRequest,
  AnalysisListResponse,
  AnalysisRecipient,
  EmailSendResult,
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
      sessionIdIsNull?: boolean;
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
    if (opts?.sessionIdIsNull === true) params.session_id_is_null = "true";
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

  /**
   * Fetch a completed analysis rendered as a PDF (binary Blob). Backend 409s
   * if the analysis isn't complete.
   */
  getPdf: (
    analysisId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGetBlob(`/analyses/${analysisId}/pdf`, token, refreshToken);
  },

  /**
   * Resolve the test driver's email for the manual-send confirmation dialog.
   */
  getRecipient: (
    analysisId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<AnalysisRecipient>(
      `/analyses/${analysisId}/recipient`,
      undefined,
      token,
      refreshToken,
    );
  },

  /**
   * Fetch the deterministic telemetry figure (SVG) for a completed session
   * analysis. {svg: null} when there is nothing to show.
   */
  getTelemetry: (
    analysisId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<{ svg: string | null }>(
      `/analyses/${analysisId}/telemetry`,
      undefined,
      token,
      refreshToken,
    );
  },

  /**
   * Manually email a completed analysis PDF to the test's driver.
   */
  sendEmail: (
    analysisId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<EmailSendResult>(
      `/analyses/${analysisId}/email`,
      {},
      token,
      refreshToken,
    );
  },
};
