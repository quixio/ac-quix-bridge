"use client";

import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import { Card } from "@/components/ui/card";
import type { Analysis } from "@/types/analysis";

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${Math.round(ms / 1000)}s`;
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

export function AnalysisCard({ analysis }: { analysis: Analysis }) {
  return (
    <Card className="p-6 space-y-6">
      <header className="text-sm text-muted-foreground">
        {analysis.id} · {analysis.session_id.slice(0, 16)}
      </header>

      {/* KPI grid */}
      {analysis.kpis.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">KPIs</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {analysis.kpis.map((k) => (
              <div key={k.name} className="p-3 rounded-md bg-muted">
                <div className="text-xs text-muted-foreground">{k.name}</div>
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
            {analysis.requirements_check.map((r, i) => (
              <div key={i} className="flex items-center gap-3 text-sm">
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
            {analysis.anomalies.map((a, i) => (
              <li key={i} className="flex items-start gap-3 text-sm">
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
                <span>{a.description}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Markdown narrative */}
      {analysis.summary_md && (
        <section className="prose prose-sm max-w-none">
          <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
            {analysis.summary_md}
          </ReactMarkdown>
        </section>
      )}

      {/* Footer */}
      <footer className="text-xs text-muted-foreground border-t pt-3 flex flex-wrap gap-3">
        {analysis.model && <span>{analysis.model}</span>}
        {analysis.tokens_in !== null &&
          analysis.tokens_in !== undefined &&
          analysis.tokens_out !== null &&
          analysis.tokens_out !== undefined && (
            <span>
              {analysis.tokens_in}→{analysis.tokens_out} tok
            </span>
          )}
        <span>{formatDuration(analysis.duration_ms)}</span>
        {analysis.quix_session_id && (
          <span>session {analysis.quix_session_id}</span>
        )}
      </footer>
    </Card>
  );
}
