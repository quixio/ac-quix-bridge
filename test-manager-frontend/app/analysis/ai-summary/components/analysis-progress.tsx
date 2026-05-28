"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import type { Analysis, AnalysisStatus } from "@/types/analysis";

const STATUS_LABEL: Record<AnalysisStatus, string> = {
  pending: "Starting up",
  running: "Running",
  fetching: "Fetching context",
  analyzing: "Analyzing telemetry",
  saving: "Saving report",
  complete: "Complete",
  failed: "Failed",
};

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? `${m}m${String(rem).padStart(2, "0")}s` : `${rem}s`;
}

interface Props {
  analysis: Analysis | null;
  fallbackStartedAt: number;
}

export function AnalysisProgress({ analysis, fallbackStartedAt }: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const startedAt = analysis
    ? new Date(analysis.created_at).getTime()
    : fallbackStartedAt;
  const elapsed = formatElapsed(now - startedAt);
  const status = analysis?.status ?? "pending";
  const label = STATUS_LABEL[status] ?? status;

  return (
    <Card className="p-6 space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
        <span className="text-sm font-medium">
          {label} · {elapsed}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        The agent is querying the lake and composing the post-race report.
        Typical runs take 1–3 minutes; the hard limit is 10.
      </p>
    </Card>
  );
}
