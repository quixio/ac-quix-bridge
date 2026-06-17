"use client";

import { Fragment, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { CheckCircle2, Download, HelpCircle, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAnalysesApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
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
  return (
    d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    }) + " UTC"
  );
}

function subtitleExtras(extra: Record<string, unknown>): string {
  const parts = [extra.driver, extra.track, extra.car_model].filter(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
  return parts.join(" · ");
}

function MetVerdict({ met }: { met: boolean | null | undefined }) {
  const base =
    "inline-flex w-28 items-center justify-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium";
  if (met === true)
    return (
      <span className={`${base} bg-[hsl(var(--success)/0.15)] text-success`}>
        <CheckCircle2 className="h-3 w-3" aria-hidden /> met
      </span>
    );
  if (met === false)
    return (
      <span
        className={`${base} bg-[hsl(var(--destructive)/0.15)] text-destructive`}
      >
        <XCircle className="h-3 w-3" aria-hidden /> unmet
      </span>
    );
  return (
    <span className={`${base} border border-border text-muted-foreground`}>
      <HelpCircle className="h-3 w-3" aria-hidden /> undetermined
    </span>
  );
}

// Semantic tokens carry light+dark variants, so badges keep contrast in both
// themes. The tokens are defined as `hsl(var(--x))` (no `<alpha-value>` channel),
// so the `/15` tint is written as an explicit arbitrary value, not `bg-info/15`.
const SEVERITY_STYLES: Record<string, string> = {
  info: "bg-[hsl(var(--info)/0.15)] text-info",
  warn: "bg-[hsl(var(--warning)/0.15)] text-warning",
  error: "bg-[hsl(var(--destructive)/0.15)] text-destructive",
};
const SEVERITY_FALLBACK = "border border-border text-muted-foreground";

function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
      <span className="h-4 w-1 rounded-full bg-primary" />
      {children}
    </h3>
  );
}

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
  const analysesApi = useAnalysesApi();
  const { toast } = useToast();
  const [isDownloading, setIsDownloading] = useState(false);

  const handleDownloadPdf = async () => {
    try {
      setIsDownloading(true);
      const blob = await analysesApi.getPdf(analysis.id);
      // Download via an anchor click, not window.open — a programmatic download
      // works inside the embedded Portal iframe and isn't blocked as a popup.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `analysis-${analysis.test_id}-${analysis.id.slice(0, 8)}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoke later so the download has time to start.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      toast({
        title: "Failed to download PDF",
        description: error instanceof Error ? error.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <Card className="overflow-hidden">
      {/* Quix brand band */}
      <div className="flex items-center justify-end bg-[#0a0b24] px-6 py-2.5">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/quix-logo.svg" alt="Quix" className="h-5 w-auto" />
      </div>
      <div className="space-y-6 p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="space-y-0.5">
          <h2 className="text-lg font-semibold">Post-Race Summary</h2>
          <p className="text-sm text-muted-foreground">
            {[
              analysis.test_id,
              analysis.session_id
                ? `Session ${formatSessionDate(analysis.session_id)}`
                : "Test-wide",
              subtitleExtras(analysis.extra),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {analysis.status === "complete" && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleDownloadPdf}
            disabled={isDownloading}
            className="shrink-0"
          >
            <Download className="mr-2 h-4 w-4" />
            {isDownloading ? "Downloading…" : "Download PDF"}
          </Button>
        )}
      </header>

      {/* KPI grid */}
      {analysis.kpis.length > 0 && (
        <section>
          <SectionHeading>KPIs</SectionHeading>
          <div
            className="grid gap-3"
            style={{
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            }}
          >
            {analysis.kpis.map((k) => (
              <div
                key={`${k.session_id ?? "_"}::${k.name}`}
                className="rounded-lg border border-border border-l-2 border-l-primary bg-background p-3"
              >
                <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {k.name}
                </div>
                {k.session_id && (
                  <div className="mt-0.5">
                    <SessionBadge sessionId={k.session_id} />
                  </div>
                )}
                <div className="mt-1 flex items-baseline gap-1">
                  <span className="text-xl font-semibold tabular-nums">
                    {k.value}
                  </span>
                  {k.unit && (
                    <span className="text-xs text-muted-foreground">
                      {k.unit}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Requirements pills */}
      {analysis.requirements_check.length > 0 && (
        <section>
          <SectionHeading>Requirements</SectionHeading>
          <div className="grid grid-cols-[max-content_1fr] items-start gap-x-3 gap-y-2 text-sm">
            {analysis.requirements_check.map((r) => (
              <Fragment key={r.requirement}>
                <MetVerdict met={r.met} />
                <div className="min-w-0">
                  <span className="font-medium">{r.requirement}</span>
                  {r.evidence && (
                    <span className="text-muted-foreground">
                      {" — "}
                      {r.evidence}
                    </span>
                  )}
                </div>
              </Fragment>
            ))}
          </div>
        </section>
      )}

      {/* Anomalies */}
      {analysis.anomalies.length > 0 && (
        <section>
          <SectionHeading>Anomalies</SectionHeading>
          <ul className="space-y-2.5">
            {analysis.anomalies.map((a) => (
              <li
                key={`${a.session_id ?? "_"}:${a.kind}:${a.lap ?? "_"}:${a.description.slice(0, 40)}`}
                className="space-y-0.5 text-sm"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                      SEVERITY_STYLES[a.severity] ?? SEVERITY_FALLBACK
                    }`}
                  >
                    {a.severity}
                  </span>
                  <span className="font-medium">{a.kind}</span>
                  {a.lap !== null && a.lap !== undefined && (
                    <span className="text-xs text-muted-foreground">
                      L{a.lap}
                    </span>
                  )}
                </div>
                <p className="min-w-0 break-words pl-0.5 text-muted-foreground">
                  {a.description}
                  <SessionBadge sessionId={a.session_id} />
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Markdown narrative */}
      {analysis.summary_md && (
        <section className="prose prose-sm max-w-none dark:prose-invert prose-headings:text-foreground prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:text-foreground">
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
      </div>
    </Card>
  );
}
