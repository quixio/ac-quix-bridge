"use client";

import { useTheme } from "next-themes";
import { useEffect, useState, useCallback } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useSettingsApi, usePortalApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { Loader2, Rocket } from "lucide-react";
import type { IntegrationSettings } from "@/lib/api/settings";
import type { DeploymentReference } from "@/lib/types/portal";
import { DeploymentPickerDialog } from "@/components/settings/deployment-picker-dialog";
import { DeploymentDisplay } from "@/components/settings/deployment-display";
import { FallbackIndicator } from "@/components/settings/fallback-indicator";

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const settingsApi = useSettingsApi();
  const portalApi = usePortalApi();
  const { toast } = useToast();

  // Integration settings state
  const [settings, setSettings] = useState<IntegrationSettings | null>(null);
  const [loadingSettings, setLoadingSettings] = useState(true);

  // Form state - deployment references
  const [configApiDeployment, setConfigApiDeployment] =
    useState<DeploymentReference | null>(null);

  // Fallback flags
  const [configApiIsFallback, setConfigApiIsFallback] = useState(false);

  // Refresh states
  const [refreshingConfig, setRefreshingConfig] = useState(false);

  // Clearing state - tracks which field is being cleared
  const [clearingField, setClearingField] = useState<string | null>(null);

  // Dialog states
  const [configPickerOpen, setConfigPickerOpen] = useState(false);

  // Avoid hydration mismatch
  useEffect(() => {
    setMounted(true);
  }, []);

  // Load settings function (extracted for reuse)
  const loadSettings = useCallback(
    async (showLoading = true) => {
      try {
        if (showLoading) setLoadingSettings(true);
        const data = await settingsApi.getSettings();
        setSettings(data);

        // Set deployment references
        setConfigApiDeployment(data.config_api_deployment || null);

        // Set fallback flags
        setConfigApiIsFallback(data.config_api_is_fallback || false);
      } catch (error) {
        console.error("Failed to load settings:", error);
        toast({
          title: "Error",
          description: "Failed to load integration settings",
          variant: "destructive",
        });
      } finally {
        if (showLoading) setLoadingSettings(false);
      }
    },
    [settingsApi, toast],
  );

  // Load settings on mount
  useEffect(() => {
    if (!mounted) return;
    loadSettings();
  }, [mounted, loadSettings]);

  // Save a field and reload settings
  const saveAndReload = useCallback(
    async (updates: Record<string, DeploymentReference | null>) => {
      try {
        await settingsApi.updateSettings(updates);
        await loadSettings(false);
        toast({
          title: "Settings saved",
          description: "Integration settings have been updated",
        });
      } catch (error) {
        console.error("Failed to save setting:", error);
        toast({
          title: "Error",
          description: "Failed to save setting",
          variant: "destructive",
        });
      }
    },
    [settingsApi, loadSettings, toast],
  );

  // Refresh deployment info from Portal API
  const refreshDeployment = useCallback(
    async (
      deployment: DeploymentReference | null,
      setDeployment: (d: DeploymentReference | null) => void,
      setRefreshing: (r: boolean) => void,
      fieldName: string,
    ) => {
      if (!deployment) return;

      try {
        setRefreshing(true);
        const deployments = await portalApi.getDeployments(
          deployment.workspace_id,
        );
        const updated = deployments.find(
          (d) => d.deploymentId === deployment.deployment_id,
        );

        if (updated) {
          const ref: DeploymentReference = {
            deployment_id: updated.deploymentId,
            workspace_id: deployment.workspace_id,
            deployment_name: updated.name,
            public_url: updated.publicUrl,
            embedded_view_url: updated.embedded_view_url,
            internal_url: updated.service_name
              ? `http://${updated.service_name}`
              : updated.publicUrl,
          };
          setDeployment(ref);
          await settingsApi.updateSettings({ [fieldName]: ref });
          await loadSettings(false);
          toast({
            title: "Deployment refreshed",
            description: `Updated info for "${updated.name}"`,
          });
        }
      } catch (error) {
        console.error("Failed to refresh deployment:", error);
        toast({
          title: "Error",
          description: "Failed to refresh deployment info",
          variant: "destructive",
        });
      } finally {
        setRefreshing(false);
      }
    },
    [portalApi, settingsApi, loadSettings, toast],
  );

  // Clear a field and reload to show fallback
  const clearAndReload = useCallback(
    async (field: "config_api_deployment") => {
      try {
        setClearingField(field);
        await settingsApi.updateSettings({ [field]: null });
        await loadSettings(false);
        toast({
          title: "Selection cleared",
          description: "Auto-detected fallback has been applied",
        });
      } catch (error) {
        console.error("Failed to clear setting:", error);
        toast({
          title: "Error",
          description: "Failed to clear setting",
          variant: "destructive",
        });
      } finally {
        setClearingField(null);
      }
    },
    [settingsApi, loadSettings, toast],
  );

  const handleConfigDeploymentConfirm = (
    deployment: DeploymentReference | null,
  ) => {
    if (deployment === null) {
      clearAndReload("config_api_deployment");
    } else {
      setConfigApiDeployment(deployment);
      setConfigApiIsFallback(false);
      saveAndReload({ config_api_deployment: deployment });
    }
  };

  if (!mounted) {
    return (
      <MainLayout>
        <div className="max-w-4xl">
          <div className="mb-6">
            <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
            <p className="text-muted-foreground">
              Manage your application preferences
            </p>
          </div>
        </div>
      </MainLayout>
    );
  }

  return (
    <MainLayout>
      <div className="max-w-4xl">
        {/* Page Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground">
            Manage your application preferences
          </p>
        </div>

        {/* Settings Content */}
        <div className="space-y-6">
          {/* Appearance Settings */}
          <Card>
            <CardHeader>
              <CardTitle>Appearance</CardTitle>
              <CardDescription>
                Customize how the Test Manager looks and feels
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <Label htmlFor="theme-select">Theme</Label>
                <Select value={theme} onValueChange={setTheme}>
                  <SelectTrigger id="theme-select" className="w-[200px]">
                    <SelectValue placeholder="Select theme" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="light">Light</SelectItem>
                    <SelectItem value="dark">Dark</SelectItem>
                    <SelectItem value="system">System</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">
                  Choose your preferred theme or use system settings
                </p>
              </div>
            </CardContent>
          </Card>

          {/* Integration Settings */}
          <Card>
            <CardHeader>
              <CardTitle>Integrations</CardTitle>
              <CardDescription>
                Configure external service connections for configurations
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-8">
              {loadingSettings ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <>
                  {/* ═══════════════════════════════════════════════════════════
                      CONFIGURATIONS SECTION
                      ═══════════════════════════════════════════════════════════ */}
                  <div className="space-y-4">
                    <div>
                      <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                        Configurations
                      </h3>
                      <p className="text-sm text-muted-foreground mt-1">
                        Dynamic Configuration Manager for test configurations
                      </p>
                    </div>

                    <FallbackIndicator
                      isFallback={configApiIsFallback}
                      message={`Auto-detected "${configApiDeployment?.deployment_name}" from current workspace`}
                    />

                    {configApiDeployment ? (
                      <DeploymentDisplay
                        deployment={configApiDeployment}
                        isFallback={configApiIsFallback}
                        isRefreshing={refreshingConfig}
                        isClearing={clearingField === "config_api_deployment"}
                        onClear={() => handleConfigDeploymentConfirm(null)}
                        onChange={() => setConfigPickerOpen(true)}
                        onRefresh={() =>
                          refreshDeployment(
                            configApiDeployment,
                            setConfigApiDeployment,
                            setRefreshingConfig,
                            "config_api_deployment",
                          )
                        }
                      />
                    ) : (
                      <Button
                        variant="outline"
                        onClick={() => setConfigPickerOpen(true)}
                        className="w-full justify-start"
                      >
                        <Rocket className="mr-2 h-4 w-4" />
                        Select Configuration API Deployment
                      </Button>
                    )}
                  </div>

                  {/* Last Updated Info */}
                  {settings?.updated_at && (
                    <p className="text-xs text-muted-foreground text-right">
                      Last updated:{" "}
                      {new Date(settings.updated_at).toLocaleString()}
                      {settings.updated_by && ` by ${settings.updated_by}`}
                    </p>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          {/* About Section */}
          <Card>
            <CardHeader>
              <CardTitle>About</CardTitle>
              <CardDescription>Application information</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Version</span>
                  <span className="font-medium">0.1.0</span>
                </div>
                <Separator />
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Environment</span>
                  <span className="font-medium">
                    {process.env.NODE_ENV === "development"
                      ? "Development"
                      : "Production"}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Deployment Picker Dialogs */}
      <DeploymentPickerDialog
        open={configPickerOpen}
        onOpenChange={setConfigPickerOpen}
        selectedDeployment={configApiDeployment}
        onConfirm={handleConfigDeploymentConfirm}
        isFallback={configApiIsFallback}
        title="Select Configuration API"
        description="Select the Dynamic Configuration Manager deployment for test configurations."
      />
    </MainLayout>
  );
}
