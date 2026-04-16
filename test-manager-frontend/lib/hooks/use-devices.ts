"use client";

import { useState, useEffect, useMemo } from "react";
import { useDevicesApi } from "./use-api";
import type { Device, DeviceQuery } from "@/types/device";
import type { PaginatedResponse, PageSize } from "@/types/pagination";

export function useDevices(initialQuery?: DeviceQuery) {
  const devicesApi = useDevicesApi();
  const [devices, setDevices] = useState<Device[]>([]);
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

    async function fetchDevices() {
      try {
        setLoading(true);
        setError(null);
        const data: PaginatedResponse<Device> = await devicesApi.list(query);

        if (!cancelled) {
          setDevices(data.items);
          setTotal(data.total);
          setTotalPages(data.total_pages);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err : new Error("Failed to fetch devices"),
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    fetchDevices();
    return () => {
      cancelled = true;
    };
  }, [queryKey, refetchTrigger, devicesApi]);

  const refetch = () => setRefetchTrigger((prev) => prev + 1);
  const goToPage = (newPage: number) => setPage(newPage);
  const changePageSize = (newPageSize: PageSize) => {
    setPageSize(newPageSize);
    setPage(1);
  };

  return {
    devices,
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

export function useDevice(deviceId: string | null) {
  const devicesApi = useDevicesApi();
  const [device, setDevice] = useState<Device | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [refetchTrigger, setRefetchTrigger] = useState(0);

  useEffect(() => {
    if (!deviceId) {
      setDevice(null);
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function fetchDevice() {
      try {
        setLoading(true);
        setError(null);
        const data = await devicesApi.get(deviceId!);
        if (!cancelled) setDevice(data);
      } catch (err) {
        if (!cancelled)
          setError(
            err instanceof Error ? err : new Error("Failed to fetch device"),
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchDevice();
    return () => {
      cancelled = true;
    };
  }, [deviceId, refetchTrigger, devicesApi]);

  const refetch = () => setRefetchTrigger((prev) => prev + 1);
  return { device, loading, error, refetch };
}
