"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import type {
  ActivityEvent,
  Analysis,
  AnalysisStatus,
} from "@/types/analysis";

// The runner no longer infers fetching/analyzing/saving sub-phases (the live
// feed shows the real steps) — status stays `running` for the whole run, so it
// gets the honest in-progress label. fetching/analyzing/saving kept for any
// legacy docs that still carry them.
const STATUS_LABEL: Record<AnalysisStatus, string> = {
  pending: "Starting up",
  running: "Analyzing telemetry",
  fetching: "Fetching context",
  analyzing: "Analyzing telemetry",
  saving: "Saving report",
  complete: "Complete",
  failed: "Failed",
};

const TERMINAL_STATUSES = new Set<AnalysisStatus>(["complete", "failed"]);

function subTag(sub?: string | null): string | null {
  if (sub === "command") return "$";
  if (sub === "file_edit") return "✎";
  if (sub === "working") return "⋯";
  return null;
}

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
  const inProgress = !TERMINAL_STATUSES.has(status);
  const activity = analysis?.activity ?? [];

  return (
    <Card className="p-6 space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
        <span className="text-sm font-medium">
          {label} · {elapsed}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        {analysis?.session_id === null
          ? "The agent is querying the lake across every session of the test and composing the cross-session report. Typical test-wide runs take 5–13 minutes; the hard limit is 15."
          : "The agent is querying the lake and composing the post-race report. Typical session runs take 4–8 minutes; the hard limit is 15."}
      </p>

      {activity.length > 0 && (
        <div className="space-y-1 border-t pt-3" aria-live="polite">
          {activity.map((e, i) => (
            <ActivityRow
              key={`${e.ts}-${i}`}
              event={e}
              running={inProgress && i === activity.length - 1 && !isDone(e)}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

function isDone(e: ActivityEvent): boolean {
  return e.error === true || e.result != null || e.kind === "agent_end";
}

function ActivityRow({
  event: e,
  running,
}: {
  event: ActivityEvent;
  running: boolean;
}) {
  const nested = e.kind === "agent_step" || e.kind === "agent_end";
  // agent_start is a section header (▸) — keep it emphasized even once done.
  const muted = !running && !nested && e.kind !== "agent_start";
  const marker = (
    <span className="flex w-3 shrink-0 items-center justify-center">
      {e.error ? (
        <span className="text-red-500">✗</span>
      ) : running ? (
        <span className="h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
      ) : (
        <span className="text-green-500">✓</span>
      )}
    </span>
  );
  const tag = subTag(e.sub);

  return (
    <div className={`flex min-w-0 items-center gap-2 text-sm ${nested ? "ml-5" : ""}`}>
      {marker}
      {e.kind === "agent_start" && (
        <span className="text-muted-foreground">▸</span>
      )}
      <span className={`shrink-0 ${muted ? "text-muted-foreground" : "font-medium"}`}>
        {e.label}
      </span>
      {tag && (
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {tag}
        </span>
      )}
      {e.detail && (
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">
          {e.detail}
        </span>
      )}
      {e.result != null && (
        <span
          className={`min-w-0 truncate text-xs ${e.error ? "text-red-500" : "text-muted-foreground"}`}
        >
          → {e.result}
        </span>
      )}
    </div>
  );
}
