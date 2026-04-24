"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { MainLayout } from "@/components/layout/main-layout";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardContent } from "@/components/ui/card";
import { Loader2 } from "lucide-react";
import {
  GitCompare,
  MapPin,
  Radio,
  BarChart3,
  Trophy,
  BookOpenText,
} from "lucide-react";
import { useTestsApi } from "@/lib/hooks/use-api";
import { LeaderboardTab } from "@/components/analysis/leaderboard-tab";

const ANALYSIS_TABS = [
  {
    value: "compare",
    label: "Compare",
    icon: GitCompare,
    title: "Compare Runs",
    description:
      "Compare laps across multiple tests to find performance tradeoffs. Overlay speed, tire temperatures, and driver inputs by track position.",
  },
  {
    value: "per-corner",
    label: "Per-Corner",
    icon: MapPin,
    title: "Per-Corner Analysis",
    description:
      "Analyze performance at specific track corners using sector metadata. See entry speed, min speed, exit speed, and time-in-corner for each run.",
  },
  {
    value: "live",
    label: "Live",
    icon: Radio,
    title: "Live Telemetry",
    description:
      "Real-time telemetry dashboard for tests currently in progress. Monitor speed, tire temps, lap splits, and driver inputs as they happen.",
  },
  {
    value: "single-run",
    label: "Single Run",
    icon: BarChart3,
    title: "Single Run Analysis",
    description:
      "Deep-dive analysis of a single test. Lap-by-lap breakdown, corner-by-corner performance, driver input traces, and telemetry small-multiples.",
  },
  {
    value: "leaderboard",
    label: "Leaderboard",
    icon: Trophy,
    title: "Leaderboard",
    description:
      "Historical best laps with real-time ghost projection. Track your fastest laps across sessions and see a live projected lap time during active tests.",
  },
  {
    value: "notebook",
    label: "Notebook",
    icon: BookOpenText,
    title: "Interactive Notebook",
    description:
      "Interactive data science notebook for advanced analysis. Currently available via Analytics in the sidebar.",
  },
] as const;

function PlaceholderTab({ tab }: { tab: (typeof ANALYSIS_TABS)[number] }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-16 text-center">
        <div className="mb-4 rounded-full bg-primary/10 p-4">
          <tab.icon className="h-8 w-8 text-primary" />
        </div>
        <h2 className="text-xl font-semibold mb-2">{tab.title}</h2>
        <span className="text-sm text-muted-foreground">Coming soon</span>
      </CardContent>
    </Card>
  );
}

function CompareTab({ testId }: { testId: string | null }) {
  const testsApi = useTestsApi();
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Telemetry Explorer base URL — resolved from env or fallback
  const explorerBaseUrl = process.env.NEXT_PUBLIC_TELEMETRY_EXPLORER_URL || "";

  useEffect(() => {
    if (!testId) {
      // No test selected — show explorer without filters
      if (explorerBaseUrl) {
        setIframeUrl(explorerBaseUrl);
      }
      return;
    }

    const fetchParams = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = await testsApi.getTelemetryParams(testId);
        const qs = new URLSearchParams();
        if (params.environment) qs.set("environment", params.environment);
        if (params.test_rig) qs.set("test_rig", params.test_rig);
        if (params.experiment) qs.set("experiment", params.experiment);
        if (params.driver) qs.set("driver", params.driver);
        if (params.track) qs.set("track", params.track);
        if (params.carModel) qs.set("carModel", params.carModel);
        setIframeUrl(`${explorerBaseUrl}?${qs.toString()}`);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load telemetry parameters",
        );
        // Fall back to unfiltered explorer
        if (explorerBaseUrl) {
          setIframeUrl(explorerBaseUrl);
        }
      } finally {
        setLoading(false);
      }
    };

    fetchParams();
  }, [testId]);

  if (!explorerBaseUrl) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-16 text-center">
          <div className="mb-4 rounded-full bg-primary/10 p-4">
            <GitCompare className="h-8 w-8 text-primary" />
          </div>
          <h2 className="text-xl font-semibold mb-2">Telemetry Explorer</h2>
          <p className="text-sm text-muted-foreground max-w-md mb-4">
            Telemetry Explorer URL is not configured. Set the
            NEXT_PUBLIC_TELEMETRY_EXPLORER_URL environment variable.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[500px]">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">Loading Telemetry Explorer...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mb-2 text-sm text-amber-500">
        Note: Could not load test parameters — showing unfiltered view.
      </div>
    );
  }

  if (!iframeUrl) return null;

  return (
    <iframe
      src={iframeUrl}
      className="w-full border-0 rounded-lg"
      style={{ height: "calc(100vh - 12rem)" }}
      title="Telemetry Explorer"
    />
  );
}

function AnalysisPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const activeTab = searchParams.get("tab") || "compare";
  const testId = searchParams.get("test_id") || null;

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.push(`/analysis?${params.toString()}`);
  };

  return (
    <MainLayout noPadding>
      <div className="w-full px-6 pt-3 pb-6">
        <h1 className="mb-3 text-3xl font-bold tracking-tight">Analysis</h1>

        <Tabs value={activeTab} onValueChange={handleTabChange}>
          <div className="flex items-center justify-between gap-4">
            <TabsList>
              {ANALYSIS_TABS.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>
                  <tab.icon className="mr-2 h-4 w-4" />
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>

            {testId && (
              <span className="rounded-md bg-muted px-2 py-1 text-sm text-muted-foreground">
                Test: {testId}
              </span>
            )}
          </div>

          <TabsContent value="compare">
            <CompareTab testId={testId} />
          </TabsContent>

          <TabsContent value="leaderboard">
            <LeaderboardTab />
          </TabsContent>

          {ANALYSIS_TABS.filter(
            (t) => t.value !== "compare" && t.value !== "leaderboard",
          ).map((tab) => (
            <TabsContent key={tab.value} value={tab.value}>
              <PlaceholderTab tab={tab} />
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </MainLayout>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense
      fallback={
        <MainLayout>
          <div className="flex items-center justify-center min-h-[500px]">
            <p className="text-muted-foreground">Loading...</p>
          </div>
        </MainLayout>
      }
    >
      <AnalysisPageContent />
    </Suspense>
  );
}
