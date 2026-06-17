"use client";

import { useState, useEffect, useMemo } from "react";
import { useExperimentsApi } from "./use-api";
import type { Experiment, ExperimentQuery } from "@/types/experiment";
import type { PaginatedResponse, PageSize } from "@/types/pagination";

export function useExperiments(initialQuery?: ExperimentQuery) {
  const experimentsApi = useExperimentsApi();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [page, setPage] = useState(initialQuery?.page || 1);
  const [pageSize, setPageSize] = useState<PageSize>(
    (initialQuery?.page_size as PageSize) || 10,
  );
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [refetchTrigger, setRefetchTrigger] = useState(0);

  const query = useMemo(
    () => ({
      ...initialQuery,
      page,
      page_size: pageSize,
    }),
    [initialQuery, page, pageSize],
  );

  const queryKey = useMemo(() => {
    const definedProps: Record<string, any> = {};
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        definedProps[key] = value;
      }
    });
    return JSON.stringify(definedProps);
  }, [query]);

  useEffect(() => {
    let cancelled = false;

    async function fetchExperiments() {
      try {
        setLoading(true);
        setError(null);
        const data: PaginatedResponse<Experiment> =
          await experimentsApi.list(query);

        if (!cancelled) {
          setExperiments(data.items);
          setTotal(data.total);
          setTotalPages(data.total_pages);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err
              : new Error("Failed to fetch experiments"),
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    fetchExperiments();
    return () => {
      cancelled = true;
    };
  }, [queryKey, refetchTrigger, experimentsApi]);

  const refetch = () => setRefetchTrigger((prev) => prev + 1);
  const goToPage = (newPage: number) => setPage(newPage);
  const changePageSize = (newPageSize: PageSize) => {
    setPageSize(newPageSize);
    setPage(1);
  };

  return {
    experiments,
    loading,
    error,
    refetch,
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  };
}

export function useExperiment(experimentId: string | null) {
  const experimentsApi = useExperimentsApi();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [refetchTrigger, setRefetchTrigger] = useState(0);

  useEffect(() => {
    if (!experimentId) {
      setExperiment(null);
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function fetchExperiment() {
      try {
        setLoading(true);
        setError(null);
        const data = await experimentsApi.get(experimentId!);
        if (!cancelled) setExperiment(data);
      } catch (err) {
        if (!cancelled)
          setError(
            err instanceof Error
              ? err
              : new Error("Failed to fetch experiment"),
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchExperiment();
    return () => {
      cancelled = true;
    };
  }, [experimentId, refetchTrigger, experimentsApi]);

  const refetch = () => setRefetchTrigger((prev) => prev + 1);
  return { experiment, loading, error, refetch };
}
