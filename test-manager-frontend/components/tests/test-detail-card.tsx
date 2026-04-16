"use client";

import { useState } from "react";
import Link from "next/link";
import { DataCard } from "@/components/shared/data-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useIntegrationsApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import { downloadCsv } from "@/lib/utils/csv";
import type { Test } from "@/types/test";
import {
  ExternalLink,
  Sliders,
  Database,
  BarChart3,
  LineChart,
  Download,
  TrendingUp,
} from "lucide-react";

interface TestDetailCardProps {
  test: Test;
  onTestUpdated?: () => void;
  resolvedNames?: { pcName: string; rigName: string; envName: string };
}

export function TestDetailCard({
  test,
  onTestUpdated,
  resolvedNames,
}: TestDetailCardProps) {
  const integrationsApi = useIntegrationsApi();
  const { formatDateTime } = useDateFormatter();
  const [isLoadingConfigUrl, setIsLoadingConfigUrl] = useState(false);
  const [isLoadingDataLakeUrl, setIsLoadingDataLakeUrl] = useState(false);
  const [isLoadingDownload, setIsLoadingDownload] = useState(false);
  const { toast } = useToast();

  const handleOpenConfigManager = async () => {
    setIsLoadingConfigUrl(true);
    try {
      const { url } = await integrationsApi.getConfigManagerUrl(test.test_id);
      window.open(url, "_blank");
    } catch (error) {
      toast({
        title: "Error loading Config Manager",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setIsLoadingConfigUrl(false);
    }
  };

  const handleOpenDataLake = async () => {
    setIsLoadingDataLakeUrl(true);
    try {
      const { url } = await integrationsApi.getDataLakeUrl(test.test_id);
      window.open(url, "_blank");
    } catch (error) {
      toast({
        title: "Error loading Data Lake",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setIsLoadingDataLakeUrl(false);
    }
  };

  const handleDownloadData = async () => {
    setIsLoadingDownload(true);
    try {
      const csvContent = await integrationsApi.downloadTestData(
        test.test_id,
        test.experiment_id,
        test.environment_id,
      );

      if (!csvContent || csvContent.trim() === "") {
        toast({
          title: "No data available",
          description: "No measurement data found for this test.",
          variant: "destructive",
        });
        return;
      }

      const timestamp = new Date()
        .toISOString()
        .replace(/[:.]/g, "-")
        .slice(0, -5);
      const filename = `test_data_${test.test_id}_${timestamp}.csv`;
      downloadCsv(csvContent, filename);

      toast({
        title: "Download started",
        description: `Downloading ${filename}`,
      });
    } catch (error) {
      toast({
        title: "Error downloading data",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setIsLoadingDownload(false);
    }
  };

  const configManagerUrl =
    test.config_id &&
    test.config_version !== null &&
    test.config_version !== undefined
      ? `/config-manager?config_id=${test.config_id}&config_version=${test.config_version}`
      : `/config-manager`;

  return (
    <div className="space-y-6">
      {/* Quick Access */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quick Access</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3">
            <Link href={`/analysis?tab=compare&test_id=${test.test_id}`}>
              <Button variant="outline" size="sm">
                <TrendingUp className="mr-2 h-4 w-4" />
                Analyze
              </Button>
            </Link>
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
          ...(test.requirements
            ? [{ label: "Requirements", value: test.requirements }]
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
                  <div className="flex gap-3 text-muted-foreground text-xs">
                    <span>{session.track}</span>
                    <span>{session.car_model}</span>
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
              disabled={isLoadingConfigUrl}
            >
              <ExternalLink className="mr-2 h-4 w-4" />
              {isLoadingConfigUrl ? "Loading..." : "View in Config Manager"}
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
