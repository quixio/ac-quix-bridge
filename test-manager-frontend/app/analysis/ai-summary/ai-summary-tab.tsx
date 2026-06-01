"use client";

import { useEffect, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useTestsApi, useAnalysesApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TestSessionPicker } from "./components/test-session-picker";
import { AnalysisCard } from "./components/analysis-card";
import { AnalysisProgress } from "./components/analysis-progress";
import { AnalyzeButton } from "./components/analyze-button";
import { useAnalysisPolling } from "./hooks/use-analysis-polling";
import type { Analysis } from "@/types/analysis";
import type { SessionInfo } from "@/types/test";

function formatHistoryLabel(a: Analysis, isLatest: boolean): string {
  const d = new Date(a.created_at);
  const ts = Number.isNaN(d.getTime())
    ? a.created_at
    : d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
  const statusTag =
    a.status === "complete"
      ? ""
      : a.status === "failed"
        ? " · failed"
        : ` · ${a.status}`;
  return `${ts}${statusTag}${isLatest ? " · latest" : ""}`;
}

interface TestRow {
  test_id: string;
  driver_name?: string | null;
}

type Mode = "session" | "test-wide";

const modeLsKey = (testId: string) => `analysis-mode:${testId}`;

export function AiSummaryTab() {
  const params = useSearchParams();
  const router = useRouter();
  // testsApi/analysesApi are useMemo'd in createAuthenticatedApi; toast is a
  // module-level reference. Stable across renders, safe in effect deps.
  const { toast } = useToast();
  const testsApi = useTestsApi();
  const analysesApi = useAnalysesApi();

  const [tests, setTests] = useState<TestRow[]>([]);
  const [sessionsByTest, setSessionsByTest] = useState<
    Record<string, SessionInfo[]>
  >({});
  const [history, setHistory] = useState<Analysis[]>([]);
  const [activeAnalysisId, setActiveAnalysisId] = useState<string | null>(null);
  const [selectedAnalysisId, setSelectedAnalysisId] = useState<string | null>(
    null,
  );
  const [analyzeStartedAt, setAnalyzeStartedAt] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const selectedTestId = params.get("test_id");
  const selectedSessionId = params.get("session_id");

  const [mode, setMode] = useState<Mode>("session");

  // Hydrate mode from localStorage when the active test changes.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!selectedTestId) {
      setMode("session");
      return;
    }
    const stored = window.localStorage.getItem(modeLsKey(selectedTestId));
    setMode(stored === "test-wide" ? "test-wide" : "session");
  }, [selectedTestId]);

  // Persist mode per test_id.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!selectedTestId) return;
    window.localStorage.setItem(modeLsKey(selectedTestId), mode);
  }, [mode, selectedTestId]);

  const handlePickerChange = useCallback(
    (sel: { testId: string | null; sessionId: string | null }) => {
      // Read from live URL instead of capturing the params snapshot so this
      // callback's identity is stable across renders. Otherwise useSearchParams
      // hands back a new object each render and TestSessionPicker's auto-pick
      // effect (deps include onChange) re-fires on every parent re-render.
      const next = new URLSearchParams(
        typeof window !== "undefined" ? window.location.search : "",
      );
      next.set("tab", "ai-summary");
      if (sel.testId) next.set("test_id", sel.testId);
      else next.delete("test_id");
      if (sel.sessionId) next.set("session_id", sel.sessionId);
      else next.delete("session_id");
      router.push(`/analysis?${next.toString()}`);
    },
    [router],
  );

  // Load tests on mount
  useEffect(() => {
    testsApi
      .list({ page: 1, page_size: 200 })
      .then((res) =>
        setTests(
          res.items.map((t) => ({ test_id: t.test_id, driver_name: t.driver })),
        ),
      )
      .catch((e) =>
        toast({
          title: "Failed to load tests",
          description: String(e),
          variant: "destructive",
        }),
      );
  }, [testsApi, toast]);

  // Load sessions for the selected test
  useEffect(() => {
    if (!selectedTestId) return;
    if (sessionsByTest[selectedTestId]) return;
    testsApi
      .get(selectedTestId)
      .then((t) =>
        setSessionsByTest((cur) => ({
          ...cur,
          [selectedTestId]: t.sessions ?? [],
        })),
      )
      .catch((e) =>
        toast({
          title: "Failed to load sessions",
          description: String(e),
          variant: "destructive",
        }),
      );
  }, [selectedTestId, sessionsByTest, testsApi, toast]);

  // Polling for the currently-running analysis
  const fetcher = useCallback(
    (id: string) => analysesApi.get(id),
    [analysesApi],
  );
  const { data: polled, error: polledError } = useAnalysisPolling(
    activeAnalysisId,
    fetcher,
  );

  // Load history of analyses for the selected (test, session) or test-wide.
  // Refetch when a running analysis reaches a terminal state so the history
  // list refreshes.
  const polledTerminal =
    polled?.status === "complete" || polled?.status === "failed";
  useEffect(() => {
    if (!selectedTestId) {
      setHistory([]);
      setSelectedAnalysisId(null);
      return;
    }
    if (mode === "session" && !selectedSessionId) {
      setHistory([]);
      setSelectedAnalysisId(null);
      return;
    }
    analysesApi
      .list(
        mode === "test-wide"
          ? { testId: selectedTestId, sessionIdIsNull: true }
          : { testId: selectedTestId, sessionId: selectedSessionId! },
      )
      .then((res) => setHistory(res.items))
      .catch((e) =>
        toast({
          title: "Failed to load history",
          description: String(e),
          variant: "destructive",
        }),
      );
  }, [
    selectedTestId,
    selectedSessionId,
    mode,
    analysesApi,
    polledTerminal,
    toast,
  ]);

  // Reset history selection when the (test, session, mode) tuple changes so we
  // default back to "latest" rather than carrying a stale id over.
  useEffect(() => {
    setSelectedAnalysisId(null);
  }, [selectedTestId, selectedSessionId, mode]);

  // Display priority: actively-polling run > user-picked from history > newest
  const pickedFromHistory =
    selectedAnalysisId != null
      ? history.find((h) => h.id === selectedAnalysisId)
      : undefined;
  const displayed: Analysis | null =
    polled ?? pickedFromHistory ?? (history.length > 0 ? history[0] : null);
  // True from POST submission until polling reports a terminal status — covers
  // the brief window after create() resolves but before the first poll returns.
  const isAnalyzing =
    activeAnalysisId !== null &&
    !(polled?.status === "complete" || polled?.status === "failed");

  const onAnalyze = useCallback(async () => {
    // Re-entry guard: isAnalyzing only flips true after the POST resolves
    // (it's keyed on activeAnalysisId), leaving a 1-2s window where users
    // can double-click and fire two analyses.
    if (isSubmitting) return;
    if (!selectedTestId) return;
    if (mode === "session" && !selectedSessionId) return;
    setIsSubmitting(true);
    setAnalyzeStartedAt(Date.now());
    try {
      const { analysis_id } = await analysesApi.create({
        test_id: selectedTestId,
        session_id: mode === "test-wide" ? null : selectedSessionId,
      });
      setActiveAnalysisId(analysis_id);
    } catch (e) {
      setAnalyzeStartedAt(null);
      toast({
        title: "Failed to start analysis",
        description: String(e),
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  }, [
    isSubmitting,
    selectedTestId,
    selectedSessionId,
    mode,
    analysesApi,
    toast,
  ]);

  const analyzeDisabled =
    !selectedTestId || (mode === "session" && !selectedSessionId);

  return (
    <div className="space-y-6 py-4">
      {selectedTestId && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Mode:</span>
          <button
            type="button"
            className={`rounded px-3 py-1 text-sm ${
              mode === "session"
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
            onClick={() => setMode("session")}
          >
            Session
          </button>
          <button
            type="button"
            className={`rounded px-3 py-1 text-sm ${
              mode === "test-wide"
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
            onClick={() => setMode("test-wide")}
          >
            Test-wide
          </button>
        </div>
      )}

      <TestSessionPicker
        tests={tests}
        sessionsByTest={sessionsByTest}
        selectedTestId={selectedTestId}
        selectedSessionId={selectedSessionId}
        onChange={handlePickerChange}
        hideSessionPicker={mode === "test-wide"}
      />

      <div className="flex justify-end">
        <AnalyzeButton
          disabled={analyzeDisabled || isSubmitting}
          isAnalyzing={isAnalyzing || isSubmitting}
          hasExistingAnalysis={history.length > 0}
          mode={mode}
          onClick={onAnalyze}
        />
      </div>

      {history.length > 1 && !polled && (
        <div className="flex items-center gap-2">
          <label className="text-sm text-muted-foreground">History</label>
          <Select
            value={selectedAnalysisId ?? history[0].id}
            onValueChange={(v) =>
              setSelectedAnalysisId(v === history[0].id ? null : v)
            }
          >
            <SelectTrigger className="w-[280px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {history.map((a, idx) => (
                <SelectItem key={a.id} value={a.id}>
                  {formatHistoryLabel(a, idx === 0)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {isAnalyzing ? (
        <AnalysisProgress
          analysis={polled}
          fallbackStartedAt={analyzeStartedAt ?? Date.now()}
        />
      ) : displayed ? (
        <AnalysisCard analysis={displayed} />
      ) : mode === "test-wide" && selectedTestId ? (
        <p className="text-sm text-muted-foreground">
          No test-wide analyses yet for this test. Click Analyze test to start
          one.
        </p>
      ) : selectedSessionId ? (
        <p className="text-sm text-muted-foreground">
          No analyses yet for this session. Click Analyze to start one.
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">
          Pick a test and a session, then click Analyze.
        </p>
      )}

      {polledError && (
        <p className="text-sm text-destructive">
          Polling failed: {polledError.message}
        </p>
      )}
    </div>
  );
}
