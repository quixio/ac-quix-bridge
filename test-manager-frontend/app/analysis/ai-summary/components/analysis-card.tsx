"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { Card } from "@/components/ui/card";
import type { Analysis } from "@/types/analysis";

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}

function formatSessionDate(sessionId: string): string {
  const d = new Date(sessionId);
  if (Number.isNaN(d.getTime())) return sessionId.slice(0, 16);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function subtitleExtras(extra: Record<string, unknown>): string {
  const parts = [extra.driver, extra.track, extra.car_model].filter(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
  return parts.join(" · ");
}

function MetVerdict({ met }: { met: boolean | null | undefined }) {
  if (met === true)
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/10 text-green-700 text-xs">
        ✓ met
      </span>
    );
  if (met === false)
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 text-red-700 text-xs">
        ✗ unmet
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-muted text-muted-foreground text-xs">
      ? undetermined
    </span>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  info: "bg-blue-500/10 text-blue-700",
  warn: "bg-amber-500/10 text-amber-700",
  error: "bg-red-500/10 text-red-700",
};

function SessionBadge({ sessionId }: { sessionId?: string | null }) {
  if (!sessionId) return null;
  const short = sessionId.replace("T", " ").slice(0, 16);
  return (
    <span className="ml-2 inline-block rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
      {short}
    </span>
  );
}

export function AnalysisCard({ analysis }: { analysis: Analysis }) {
  return (
    <Card className="p-6 space-y-6">
      <header className="space-y-0.5">
        <h2 className="text-lg font-semibold">Post-Race Summary</h2>
        <p className="text-sm text-muted-foreground">
          {[
            analysis.test_id,
            analysis.session_id
              ? formatSessionDate(analysis.session_id)
              : "Test-wide",
            subtitleExtras(analysis.extra),
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>

      {/* KPI grid */}
      {analysis.kpis.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">KPIs</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {analysis.kpis.map((k) => (
              <div
                key={`${k.session_id ?? "_"}::${k.name}`}
                className="p-3 rounded-md bg-muted"
              >
                <div className="text-xs text-muted-foreground">
                  {k.name}
                  <SessionBadge sessionId={k.session_id} />
                </div>
                <div className="text-lg font-semibold">{k.value}</div>
                {k.unit && (
                  <div className="text-xs text-muted-foreground">{k.unit}</div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Requirements pills */}
      {analysis.requirements_check.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">Requirements</h3>
          <div className="space-y-1.5">
            {analysis.requirements_check.map((r) => (
              <div
                key={r.requirement}
                className="flex items-center gap-3 text-sm"
              >
                <MetVerdict met={r.met} />
                <span>{r.requirement}</span>
                {r.evidence && (
                  <span className="text-xs text-muted-foreground">
                    — {r.evidence}
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Anomalies */}
      {analysis.anomalies.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">Anomalies</h3>
          <ul className="space-y-1.5">
            {analysis.anomalies.map((a) => (
              <li
                key={`${a.session_id ?? "_"}:${a.kind}:${a.lap ?? "_"}:${a.description.slice(0, 40)}`}
                className="flex items-start gap-3 text-sm"
              >
                <span
                  className={`inline-flex shrink-0 px-2 py-0.5 rounded-full text-xs ${
                    SEVERITY_STYLES[a.severity] ?? ""
                  }`}
                >
                  {a.severity}
                </span>
                <span className="font-mono text-xs">{a.kind}</span>
                {a.lap !== null && a.lap !== undefined && (
                  <span className="text-xs text-muted-foreground">
                    L{a.lap}
                  </span>
                )}
                <span>
                  {a.description}
                  <SessionBadge sessionId={a.session_id} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Markdown narrative */}
      {analysis.summary_md && (
        <section className="prose prose-sm max-w-none dark:prose-invert">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeSanitize]}
          >
            {analysis.summary_md}
          </ReactMarkdown>
        </section>
      )}

      {/* Footer */}
      <footer className="text-xs text-muted-foreground border-t pt-3 flex flex-wrap gap-3">
        {analysis.model && <span>{analysis.model}</span>}
        {analysis.duration_ms != null && (
          <span>Generated in {formatDuration(analysis.duration_ms)}</span>
        )}
      </footer>
    </Card>
  );
}
