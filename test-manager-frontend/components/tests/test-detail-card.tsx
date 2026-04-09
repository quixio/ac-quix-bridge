"use client"

import { useState } from "react"
import Link from "next/link"
import { DataCard } from "@/components/shared/data-card"
import { TestStatusBadge } from "./test-status-badge"
import { JsonEditor } from "@/components/shared/json-editor"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useTestsApi, useIntegrationsApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import { downloadCsv } from "@/lib/utils/csv"
import type { Test } from "@/types/test"
import { Save, X, ExternalLink, Sliders, Database, BarChart3, LineChart, Download } from "lucide-react"

interface TestDetailCardProps {
  test: Test
  onTestUpdated?: () => void
  resolvedNames?: { pcName: string; rigName: string; envName: string }
}

export function TestDetailCard({ test, onTestUpdated, resolvedNames }: TestDetailCardProps) {
  const testsApi = useTestsApi()
  const integrationsApi = useIntegrationsApi()
  const { formatDateTime } = useDateFormatter()
  const [showSensors, setShowSensors] = useState(false)
  const [sensorsJson, setSensorsJson] = useState<string>("")
  const [originalSensors, setOriginalSensors] = useState<string>("")
  const [isValidJson, setIsValidJson] = useState(true)
  const [hasChanges, setHasChanges] = useState(false)
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isLoadingConfigUrl, setIsLoadingConfigUrl] = useState(false)
  const [isLoadingDataLakeUrl, setIsLoadingDataLakeUrl] = useState(false)
  const [isLoadingDownload, setIsLoadingDownload] = useState(false)
  const { toast } = useToast()

  const handleToggleSensors = () => {
    if (!showSensors) {
      // Opening - initialize JSON
      const jsonString = JSON.stringify(test.sensors || {}, null, 2)
      setSensorsJson(jsonString)
      setOriginalSensors(jsonString)
      setIsValidJson(true)
      setHasChanges(false)
    }
    setShowSensors(!showSensors)
  }

  const handleSensorsChange = (value: string) => {
    setSensorsJson(value)

    // Validate JSON
    try {
      if (value.trim()) {
        JSON.parse(value)
      }
      setIsValidJson(true)
    } catch {
      setIsValidJson(false)
    }

    // Check if changed
    setHasChanges(value !== originalSensors)
  }

  const handleSave = async () => {
    setIsSaving(true)
    try {
      // Parse JSON (allow empty/null)
      const parsedSensors = sensorsJson.trim()
        ? JSON.parse(sensorsJson)
        : {}

      await testsApi.update(test.test_id, { sensors: parsedSensors })

      toast({
        title: "Sensors updated",
        description: "Test sensors configuration has been updated successfully.",
      })

      // Update original to reflect saved state
      setOriginalSensors(sensorsJson)
      setHasChanges(false)
      setShowConfirmDialog(false)

      // Notify parent to refresh
      if (onTestUpdated) {
        onTestUpdated()
      }
    } catch (error) {
      toast({
        title: "Error updating sensors",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleCancel = () => {
    setSensorsJson(originalSensors)
    setHasChanges(false)
    setIsValidJson(true)
  }

  const handleOpenConfigManager = async () => {
    setIsLoadingConfigUrl(true)
    try {
      const { url } = await integrationsApi.getConfigManagerUrl(test.test_id)
      window.open(url, "_blank")
    } catch (error) {
      toast({
        title: "Error loading Config Manager",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsLoadingConfigUrl(false)
    }
  }

  const handleOpenDataLake = async () => {
    setIsLoadingDataLakeUrl(true)
    try {
      const { url } = await integrationsApi.getDataLakeUrl(test.test_id)
      window.open(url, "_blank")
    } catch (error) {
      toast({
        title: "Error loading Data Lake",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsLoadingDataLakeUrl(false)
    }
  }

  const handleDownloadData = async () => {
    setIsLoadingDownload(true)
    try {
      // Call API to get test data (returns CSV text directly)
      const csvContent = await integrationsApi.downloadTestData(
        test.test_id,
        test.experiment_id,
        test.environment_id
      )

      // Check if data is empty
      if (!csvContent || csvContent.trim() === "") {
        toast({
          title: "No data available",
          description: "No measurement data found for this test.",
          variant: "destructive",
        })
        return
      }

      // Generate filename with timestamp
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, -5)
      const filename = `test_data_${test.test_id}_${timestamp}.csv`

      // Trigger download
      downloadCsv(csvContent, filename)

      toast({
        title: "Download started",
        description: `Downloading ${filename}`,
      })
    } catch (error) {
      toast({
        title: "Error downloading data",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    } finally {
      setIsLoadingDownload(false)
    }
  }

  // Build config manager URL with context
  const configManagerUrl = test.config_id && test.config_version !== null && test.config_version !== undefined
    ? `/config-manager?config_id=${test.config_id}&config_version=${test.config_version}`
    : `/config-manager`

  return (
    <div className="space-y-6">
      {/* Quick Access */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quick Access</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3">
            <Link href={configManagerUrl}>
              <Button
                variant="outline"
                size="sm"
              >
                <Sliders className="mr-2 h-4 w-4" />
                Configurations
              </Button>
            </Link>
            <Button
              variant="outline"
              size="sm"
              onClick={handleOpenDataLake}
              disabled={isLoadingDataLakeUrl}
            >
              <Database className="mr-2 h-4 w-4" />
              Data Lake
            </Button>
            <Link href={`/measurements?test_id=${test.test_id}&experiment_id=${test.experiment_id}&environment_id=${test.environment_id}`}>
              <Button
                variant="outline"
                size="sm"
              >
                <BarChart3 className="mr-2 h-4 w-4" />
                Query Data
              </Button>
            </Link>
            <Link href={`/analytics?test_id=${test.test_id}&experiment_id=${test.experiment_id}&environment_id=${test.environment_id}`}>
              <Button
                variant="outline"
                size="sm"
              >
                <LineChart className="mr-2 h-4 w-4" />
                Analytics
              </Button>
            </Link>
            <Button
              variant="outline"
              size="sm"
              onClick={handleDownloadData}
              disabled={isLoadingDownload}
              className="ml-auto"
            >
              <Download className="mr-2 h-4 w-4" />
              {isLoadingDownload ? "Downloading..." : "Download Data"}
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
          { label: "PC (Hostname)", value: resolvedNames?.pcName || test.pc_device_id },
          { label: "Test Rig", value: resolvedNames?.rigName || test.test_rig_device_id },
          { label: "Environment", value: resolvedNames?.envName || test.environment_id },
          ...(test.requirements ? [{ label: "Requirements", value: test.requirements }] : []),
        ]}
      />

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
        <CardContent className="space-y-4">
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* Config Type */}
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">Config Type</dt>
              <dd className="text-sm">{test.config_type || "Not set"}</dd>
            </div>

            {/* Config Id */}
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">Config Id</dt>
              <dd className="text-sm font-mono text-xs">
                {test.config_id || "Not set"}
              </dd>
            </div>

            {/* Target Key */}
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">Target Key</dt>
              <dd className="text-sm">{test.target_key || "Not set"}</dd>
            </div>

            {/* Config Version */}
            <div className="flex flex-col space-y-1">
              <dt className="text-sm font-medium text-muted-foreground">Config Version</dt>
              <dd className="text-sm">
                {test.config_version ?? "Not set"}
              </dd>
            </div>
          </dl>

        </CardContent>
      </Card>

      {/* Timestamps */}
      <DataCard
        title="Timestamps"
        items={[
          {
            label: "Created",
            value: formatDateTime(test.created_at),
          },
          {
            label: "Updated",
            value: formatDateTime(test.updated_at),
          },
        ]}
      />

      {/* Save Confirmation Dialog */}
      <AlertDialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Update sensors configuration?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to update the sensors configuration for this test?
              This action will modify the test data.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isSaving}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleSave} disabled={isSaving}>
              {isSaving ? "Saving..." : "Save Changes"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
