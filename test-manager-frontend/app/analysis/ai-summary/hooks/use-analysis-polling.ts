"use client";

import { useEffect, useRef, useState } from "react";
import type { Analysis } from "@/types/analysis";

const POLL_INTERVAL_MS = 3000;
const BACKOFF_AFTER_MS = 60_000;
const BACKOFF_INTERVAL_MS = 5000;
const MAX_POLLS = 140;

const TERMINAL_STATUSES = new Set(["complete", "failed"]);

export function useAnalysisPolling(
  analysisId: string | null,
  fetcher: (id: string) => Promise<Analysis>,
) {
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const pollCount = useRef(0);
  const startedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!analysisId) return;

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    pollCount.current = 0;
    startedAt.current = Date.now();
    setData(null);
    setError(null);

    const tick = async () => {
      if (cancelled) return;
      if (pollCount.current >= MAX_POLLS) return;
      pollCount.current += 1;

      try {
        const result = await fetcher(analysisId);
        if (cancelled) return;
        setData(result);
        if (TERMINAL_STATUSES.has(result.status)) {
          return; // stop scheduling further polls
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        return;
      }

      const elapsed = Date.now() - (startedAt.current ?? Date.now());
      const interval =
        elapsed > BACKOFF_AFTER_MS ? BACKOFF_INTERVAL_MS : POLL_INTERVAL_MS;
      timeoutId = setTimeout(tick, interval);
    };

    tick();
    return () => {
      cancelled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
    };
  }, [analysisId, fetcher]);

  return { data, error };
}
