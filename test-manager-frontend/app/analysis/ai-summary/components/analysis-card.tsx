"use client";

import { Fragment, useState, type ReactNode } from "react";
import { TelemetrySection } from "./telemetry-section";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  HelpCircle,
  Mail,
  XCircle,
} from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAnalysesApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import type { Analysis, AnalysisRecipient } from "@/types/analysis";

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
    d.toLocaleString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "UTC",
    }) + " UTC"
  );
}

// Subheading extras: prefer backend-stamped context (reliable driver/track/car),
// fall back to the agent's free-form `extra` for legacy/pre-context docs.
function subtitleExtras(analysis: Analysis): string {
  const c = analysis.context;
  const vals =
    c && (c.driver || c.track || c.car_model)
      ? [c.driver, c.track, c.car_model]
      : [analysis.extra.driver, analysis.extra.track, analysis.extra.car_model];
  return vals
    .filter((v): v is string => typeof v === "string" && v.length > 0)
    .join(" · ");
}

// Compact session date for filenames → "15Jun2026" (matches the backend's
// %-d%b%Y); null when missing/unparseable.
function compactDate(sessionId: string | null): string | null {
  if (!sessionId) return null;
  const d = new Date(sessionId);
  if (Number.isNaN(d.getTime())) return null;
  // getUTCDate/Year are zero-free + deterministic (Intl day-padding is
  // implementation-defined); matches the backend's f"{dt.day}{%b%Y}".
  const day = String(d.getUTCDate());
  const mon = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  const year = String(d.getUTCFullYear());
  return `${day}${mon}${year}`;
}

// PDF download filename — mirrors the backend `analysis_pdf_filename`.
function pdfFilename(analysis: Analysis): string {
  const track = analysis.context?.track;
  const date = compactDate(analysis.session_id);
  const stem =
    track && date
      ? `Quix-Post-Race-${track}-${date}`
      : `Quix-Post-Race-Analysis-${analysis.test_id}`;
  return stem.replace(/[^A-Za-z0-9._-]/g, "_") + ".pdf";
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

export function SectionHeading({ children }: { children: ReactNode }) {
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
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [recipient, setRecipient] = useState<AnalysisRecipient | null>(null);
  const [recipientError, setRecipientError] = useState(false);
  const [loadingRecipient, setLoadingRecipient] = useState(false);
  const [isSending, setIsSending] = useState(false);

  // Open the confirm dialog and resolve the driver email to show in it.
  const openSendDialog = async () => {
    setRecipient(null);
    setRecipientError(false);
    setConfirmOpen(true);
    setLoadingRecipient(true);
    try {
      setRecipient(await analysesApi.getRecipient(analysis.id));
    } catch {
      // A fetch failure is distinct from "driver has no email" — surface it.
      setRecipientError(true);
    } finally {
      setLoadingRecipient(false);
    }
  };

  const handleSendEmail = async () => {
    try {
      setIsSending(true);
      const res = await analysesApi.sendEmail(analysis.id);
      toast({
        title: "Email sent",
        description: `Report sent to ${res.email}`,
      });
      setConfirmOpen(false);
    } catch (error) {
      toast({
        title: "Failed to send email",
        description: error instanceof Error ? error.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setIsSending(false);
    }
  };

  const handleDownloadPdf = async () => {
    try {
      setIsDownloading(true);
      const blob = await analysesApi.getPdf(analysis.id);
      // Download via an anchor click, not window.open — a programmatic download
      // works inside the embedded Portal iframe and isn't blocked as a popup.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = pdfFilename(analysis);
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

  const subtitle = [
    analysis.test_id,
    analysis.session_id
      ? `Session ${formatSessionDate(analysis.session_id)}`
      : "Test-wide",
    subtitleExtras(analysis),
  ]
    .filter(Boolean)
    .join(" · ");

  // Anything that isn't a finished report (failed, or a stale/orphaned run that
  // never saved) has no KPIs/narrative — render a clear state instead of an
  // empty "Post-Race Analysis" shell.
  if (analysis.status !== "complete") {
    const failed = analysis.status === "failed";
    const title = failed ? "Analysis failed" : "Analysis didn't finish";
    const detail =
      analysis.error ||
      (failed
        ? "The agent didn't finish before the run ended."
        : "This run stalled and never produced a report — it may have been interrupted.");
    const tag = analysis.error_kind || (failed ? null : analysis.status);
    return (
      <Card className="overflow-hidden">
        <div className="flex items-center justify-end bg-[#0a0b24] px-6 py-2.5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/quix-logo.svg" alt="Quix" className="h-5 w-auto" />
        </div>
        <div className="space-y-4 p-6">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--destructive)/0.12)]">
              <AlertTriangle className="h-5 w-5 text-destructive" aria-hidden />
            </div>
            <div className="space-y-0.5">
              <h2 className="text-lg font-semibold">{title}</h2>
              <p className="text-sm text-muted-foreground">{subtitle}</p>
            </div>
          </div>
          <div className="rounded-md border border-[hsl(var(--destructive)/0.3)] bg-[hsl(var(--destructive)/0.06)] p-3">
            <p className="break-words text-sm text-foreground">{detail}</p>
            {tag && (
              <span className="mt-1.5 inline-block rounded bg-[hsl(var(--destructive)/0.15)] px-1.5 py-0.5 font-mono text-xs text-destructive">
                {tag}
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            Use <span className="font-medium text-foreground">Re-analyze</span>{" "}
            above to try again.
          </p>
          <footer className="flex flex-wrap gap-3 border-t pt-3 text-xs text-muted-foreground">
            {analysis.model && <span>{analysis.model}</span>}
            {analysis.duration_ms != null && (
              <span>
                {failed ? "Failed" : "Stopped"} after{" "}
                {formatDuration(analysis.duration_ms)}
              </span>
            )}
          </footer>
        </div>
      </Card>
    );
  }

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
            <h2 className="text-lg font-semibold">Post-Race Analysis</h2>
            <p className="text-sm text-muted-foreground">{subtitle}</p>
          </div>
          {analysis.status === "complete" && (
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleDownloadPdf}
                disabled={isDownloading}
              >
                <Download className="mr-2 h-4 w-4" />
                {isDownloading ? "Downloading…" : "Download PDF"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={openSendDialog}
                disabled={loadingRecipient || isSending}
              >
                <Mail className="mr-2 h-4 w-4" />
                Send to driver
              </Button>
            </div>
          )}
        </header>

        <AlertDialog
          open={confirmOpen}
          onOpenChange={(open) => {
            // Don't let a backdrop/Escape dismiss interrupt an in-flight send.
            if (!isSending) setConfirmOpen(open);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Send report to driver?</AlertDialogTitle>
              <AlertDialogDescription>
                {loadingRecipient
                  ? "Resolving the driver's email…"
                  : recipientError
                    ? "Couldn't load the driver's email — please try again."
                    : recipient?.has_email
                      ? `This will email the post-race PDF to ${recipient.email}.`
                      : "This test's driver has no email on file, so the report can't be sent."}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={isSending}>Cancel</AlertDialogCancel>
              {recipient?.has_email && (
                <AlertDialogAction
                  onClick={(e) => {
                    // Keep the dialog open until the async send resolves.
                    e.preventDefault();
                    void handleSendEmail();
                  }}
                  disabled={isSending}
                  aria-busy={isSending}
                >
                  {isSending ? "Sending…" : "Send"}
                </AlertDialogAction>
              )}
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

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
                  key={`${a.session_id ?? "_"}:${a.kind}:${
                    a.lap ?? "_"
                  }:${a.description.slice(0, 40)}`}
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

        <TelemetrySection analysisId={analysis.id} status={analysis.status} />

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
