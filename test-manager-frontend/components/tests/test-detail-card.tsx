"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DataCard } from "@/components/shared/data-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTestsApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import type { SessionInfo, Test } from "@/types/test";
import { Sliders, TrendingUp, Sparkles, Database } from "lucide-react";

interface TestDetailCardProps {
  test: Test;
  resolvedNames?: { pcName: string; rigName: string; envName: string };
}

export function TestDetailCard({ test, resolvedNames }: TestDetailCardProps) {
  const testsApi = useTestsApi();
  const router = useRouter();
  const { formatDateTime } = useDateFormatter();
  const [isOpeningAnalysis, setIsOpeningAnalysis] = useState(false);
  const { toast } = useToast();

  const handleOpenConfigManager = () => {
    // In-app, platform-agnostic: open the Configurations page deep-linked to this
    // test's config + version (the page resolves the DCM URL from the Portal API /
    // stored settings). Replaces the old hardcoded portal.cloud.quix.io tab.
    const url =
      test.config_id && test.config_version !== null
        ? `/config-manager?config_id=${test.config_id}&config_version=${test.config_version}`
        : "/config-manager";
    router.push(url);
  };

  const handleAnalyze = async () => {
    setIsOpeningAnalysis(true);
    try {
      // Pre-fetch telemetry params so we fail here (with a toast) instead of
      // navigating to the analysis page only to show an unfiltered fallback.
      await testsApi.getTelemetryParams(test.test_id);
      router.push(`/analysis?tab=compare&test_id=${test.test_id}`);
    } catch (error) {
      toast({
        title: "Cannot open analysis",
        description:
          error instanceof Error
            ? error.message
            : "Configuration service is unavailable. Please try again shortly.",
        variant: "destructive",
      });
    } finally {
      setIsOpeningAnalysis(false);
    }
  };

  const handleAiSummary = (sessionId?: string) => {
    const params = new URLSearchParams();
    params.set("tab", "ai-summary");
    params.set("test_id", test.test_id);
    if (sessionId) params.set("session_id", sessionId);
    router.push(`/analysis?${params.toString()}`);
  };

  const handleAnalyzeSession = (session: SessionInfo) => {
    const params = new URLSearchParams();
    params.set("tab", "compare");
    params.set("test_id", test.test_id);
    params.set("session_id", session.session_id);
    // Pass the session's OWN track/car (a test can hold sessions on different
    // tracks/cars) so the Explorer deep-links to the exact partition path.
    if (session.track) params.set("track", session.track);
    if (session.car_model) params.set("carModel", session.car_model);
    router.push(`/analysis?${params.toString()}`);
  };

  const handleViewInLakehouse = (session?: SessionInfo) => {
    const params = new URLSearchParams();
    params.set("test_id", test.test_id);
    if (session) {
      params.set("session_id", session.session_id);
      if (session.track) params.set("track", session.track);
      if (session.car_model) params.set("carModel", session.car_model);
    }
    router.push(`/lakehouse?${params.toString()}`);
  };

  return (
    <div className="space-y-6">
      {/* Quick Access */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quick Access</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              onClick={handleAnalyze}
              disabled={isOpeningAnalysis}
            >
              <TrendingUp className="mr-2 h-4 w-4" />
              {isOpeningAnalysis ? "Opening..." : "Analyze"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleAiSummary()}
            >
              <Sparkles className="mr-2 h-4 w-4" />
              AI Summary
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleViewInLakehouse()}
            >
              <Database className="mr-2 h-4 w-4" />
              View Data
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Test Setup */}
      <DataCard
        title="Test Setup"
        items={[
          { label: "Experiment", value: test.experiment_id },
          { label: "Driver", value: test.driver },
          {
            label: "PC (Hostname)",
            value: resolvedNames?.pcName || test.pc_device_id,
          },
          {
            label: "Test Rig",
            value: resolvedNames?.rigName || test.test_rig_device_id,
          },
          {
            label: "Environment",
            value: resolvedNames?.envName || test.environment_id,
          },
          {
            label: "Mode",
            value: test.mode
              ? test.mode.charAt(0).toUpperCase() + test.mode.slice(1)
              : "—",
          },
          ...(test.requirements
            ? [
                {
                  label: "Requirements",
                  value: test.requirements,
                  className: "sm:col-span-2",
                  valueClassName: "whitespace-pre-line",
                },
              ]
            : []),
        ]}
      />

      {/* Sessions */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sessions</CardTitle>
        </CardHeader>
        <CardContent>
          {test.sessions && test.sessions.length > 0 ? (
            <div className="space-y-2">
              {test.sessions.map((session) => (
                <div
                  key={session.session_id}
                  className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                >
                  <span className="font-mono text-xs">
                    {session.session_id}
                  </span>
                  <div className="flex items-center gap-3 text-muted-foreground text-xs">
                    <span>{session.track}</span>
                    <span>{session.car_model}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => handleAnalyzeSession(session)}
                    >
                      <TrendingUp className="mr-1 h-3 w-3" />
                      Analyze
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => handleAiSummary(session.session_id)}
                    >
                      <Sparkles className="mr-1 h-3 w-3" />
                      AI Summary
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => handleViewInLakehouse(session)}
                    >
                      <Database className="mr-1 h-3 w-3" />
                      Data
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No sessions yet</p>
          )}
        </CardContent>
      </Card>

      {/* Configuration */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Configuration</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={handleOpenConfigManager}
            >
              <Sliders className="mr-2 h-4 w-4" />
              View in Config Manager
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">
                Config Type
              </dt>
              <dd className="text-sm">{test.config_type || "Not set"}</dd>
            </div>
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">
                Config Id
              </dt>
              <dd className="text-sm font-mono text-xs">
                {test.config_id || "Not set"}
              </dd>
            </div>
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">
                Target Key
              </dt>
              <dd className="text-sm">{test.target_key || "Not set"}</dd>
            </div>
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">
                Config Version
              </dt>
              <dd className="text-sm">{test.config_version ?? "Not set"}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* Timestamps */}
      <DataCard
        title="Timestamps"
        items={[
          { label: "Created", value: formatDateTime(test.created_at) },
          { label: "Updated", value: formatDateTime(test.updated_at) },
        ]}
      />
    </div>
  );
}
