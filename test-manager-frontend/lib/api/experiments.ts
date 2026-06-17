/**
 * API client for Experiments
 *
 * @internal - Do not import directly. Use the `useExperimentsApi()` hook instead.
 */

import { apiGet, apiPost, apiDelete } from "./client";
import type {
  Experiment,
  ExperimentCreate,
  ExperimentQuery,
} from "@/types/experiment";
import type { PaginatedResponse } from "@/types/pagination";

export const experimentsApi = {
  list: (
    params?: ExperimentQuery,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<PaginatedResponse<Experiment>>(
      "/experiments",
      params,
      token,
      refreshToken,
    );
  },

  get: (
    experimentId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiGet<Experiment>(
      `/experiments/${experimentId}`,
      undefined,
      token,
      refreshToken,
    );
  },

  create: (
    data: ExperimentCreate,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiPost<Experiment>("/experiments", data, token, refreshToken);
  },

  delete: (
    experimentId: string,
    token?: string | null,
    refreshToken?: () => Promise<string | null>,
  ) => {
    return apiDelete(`/experiments/${experimentId}`, token, refreshToken);
  },
};
