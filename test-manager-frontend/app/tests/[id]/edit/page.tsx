"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useTest } from "@/lib/hooks/use-tests";
import {
  useTestsApi,
  useDevicesApi,
  useDriversApi,
  useEnvironmentsApi,
  useExperimentsApi,
} from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { AddDriverDialog } from "@/components/drivers/add-driver-dialog";
import { AddExperimentDialog } from "@/components/experiments/add-experiment-dialog";
import { DeviceCategory } from "@/types/device";
import type { Device } from "@/types/device";
import type { Driver } from "@/types/driver";
import type { Environment } from "@/types/environment";
import type { Experiment } from "@/types/experiment";
import type { TestMode } from "@/types/test";

export default function EditTestPage() {
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const testsApi = useTestsApi();
  const devicesApi = useDevicesApi();
  const driversApi = useDriversApi();
  const environmentsApi = useEnvironmentsApi();
  const experimentsApi = useExperimentsApi();
  const testId = params.id as string;
  const { test, loading } = useTest(testId);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Form state (null = not yet edited, use test value). Experiment is editable
  // here like driver/environment — reassigning a test to a different experiment
  // is the same partition-affecting action. The experiment NAME itself stays
  // immutable (no rename) at the entity level.
  const [experimentId, setExperimentId] = useState<string | null>(null);
  const [pendingExperiment, setPendingExperiment] = useState<string | null>(
    null,
  );
  const [pcDeviceId, setPcDeviceId] = useState<string | null>(null);
  const [testRigDeviceId, setTestRigDeviceId] = useState<string | null>(null);
  const [environmentId, setEnvironmentId] = useState<string | null>(null);
  const [driver, setDriver] = useState<string | null>(null);
  const [pendingDriver, setPendingDriver] = useState<string | null>(null);
  const [requirements, setRequirements] = useState<string | null>(null);
  const [mode, setMode] = useState<TestMode | null>(null);

  // Dropdown data
  const [pcDevices, setPcDevices] = useState<Device[]>([]);
  const [testRigDevices, setTestRigDevices] = useState<Device[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [pcRes, rigRes, drvRes, envRes, expRes] = await Promise.all([
          devicesApi.list({ category: DeviceCategory.PC, page_size: 100 }),
          devicesApi.list({
            category: DeviceCategory.TEST_RIG,
            page_size: 100,
          }),
          driversApi.list({ page_size: 100 }),
          environmentsApi.list({ page_size: 100 }),
          experimentsApi.list({ page_size: 100 }),
        ]);
        setPcDevices(pcRes.items);
        setTestRigDevices(rigRes.items);
        setDrivers(drvRes.items);
        setEnvironments(envRes.items);
        setExperiments(expRes.items);
      } catch (error) {
        console.error("Failed to fetch dropdown data:", error);
      }
    };
    fetchData();
  }, []);

  // Select a just-created driver once the refetched list contains its
  // SelectItem — avoids the Radix preselect race (setting value in the same
  // batch as setDrivers fires onValueChange("") and clears it in prod).
  useEffect(() => {
    if (pendingDriver && drivers.some((d) => d.name === pendingDriver)) {
      setDriver(pendingDriver);
      setPendingDriver(null);
    }
  }, [drivers, pendingDriver]);
  // Same preselect-race guard for a just-created experiment.
  useEffect(() => {
    if (
      pendingExperiment &&
      experiments.some((x) => x.name === pendingExperiment)
    ) {
      setExperimentId(pendingExperiment);
      setPendingExperiment(null);
    }
  }, [experiments, pendingExperiment]);

  const formExperimentId = experimentId ?? test?.experiment_id ?? "";
  const formPcDeviceId = pcDeviceId ?? test?.pc_device_id ?? "";
  const formTestRigDeviceId = testRigDeviceId ?? test?.test_rig_device_id ?? "";
  const formEnvironmentId = environmentId ?? test?.environment_id ?? "";
  const formDriver = driver ?? test?.driver ?? "";
  const formRequirements = requirements ?? test?.requirements ?? "";
  const formMode = mode ?? test?.mode ?? "";

  const isDirty =
    formExperimentId !== (test?.experiment_id ?? "") ||
    formPcDeviceId !== (test?.pc_device_id ?? "") ||
    formTestRigDeviceId !== (test?.test_rig_device_id ?? "") ||
    formEnvironmentId !== (test?.environment_id ?? "") ||
    formDriver !== (test?.driver ?? "") ||
    formRequirements !== (test?.requirements ?? "") ||
    formMode !== (test?.mode ?? "");

  // mode is required in the UI; a legacy test with no mode must get one before save.
  const isValid = formMode !== "";

  const refetchDrivers = async () => {
    try {
      const res = await driversApi.list({ page_size: 100 });
      setDrivers(res.items);
    } catch (error) {
      console.error("Failed to refetch drivers:", error);
    }
  };

  const refetchExperiments = async () => {
    try {
      const res = await experimentsApi.list({ page_size: 100 });
      setExperiments(res.items);
    } catch (error) {
      console.error("Failed to refetch experiments:", error);
    }
  };

  const handleExperimentCreated = async (created: Experiment) => {
    setPendingExperiment(created.name);
    await refetchExperiments();
  };

  const handleDriverCreated = async (created: Driver) => {
    setPendingDriver(created.name);
    await refetchDrivers();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid) return;

    try {
      setIsSubmitting(true);
      await testsApi.update(testId, {
        experiment_id: formExperimentId,
        pc_device_id: formPcDeviceId,
        test_rig_device_id: formTestRigDeviceId,
        environment_id: formEnvironmentId,
        driver: formDriver,
        requirements: formRequirements,
        mode: formMode as TestMode,
      });

      toast({
        title: "Test Updated",
        description: `Test ${testId} has been updated.`,
      });

      router.push(`/tests/${testId}`);
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error ? error.message : "Failed to update test.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (loading || !test) {
    return (
      <MainLayout
        backLink={{ href: `/tests/${testId}`, label: "Back to Test" }}
      >
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-96 w-full" />
        </div>
      </MainLayout>
    );
  }

  return (
    <MainLayout backLink={{ href: `/tests/${testId}`, label: "Back to Test" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Edit Test: {testId}</h1>

        <Card>
          <CardHeader>
            <CardTitle>Test Setup</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label>PC (Hostname) *</Label>
                <Select value={formPcDeviceId} onValueChange={setPcDeviceId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select PC" />
                  </SelectTrigger>
                  <SelectContent>
                    {pcDevices.map((d) => (
                      <SelectItem key={d.device_id} value={d.device_id}>
                        {d.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>Test Rig *</Label>
                <Select
                  value={formTestRigDeviceId}
                  onValueChange={setTestRigDeviceId}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select test rig" />
                  </SelectTrigger>
                  <SelectContent>
                    {testRigDevices.map((d) => (
                      <SelectItem key={d.device_id} value={d.device_id}>
                        {d.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>Environment *</Label>
                <Select
                  value={formEnvironmentId}
                  onValueChange={setEnvironmentId}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select environment" />
                  </SelectTrigger>
                  <SelectContent>
                    {environments.map((e) => (
                      <SelectItem
                        key={e.environment_id}
                        value={e.environment_id}
                      >
                        {e.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>Experiment *</Label>
                <div className="flex items-center gap-2">
                  <div className="flex-1">
                    <Select
                      value={formExperimentId}
                      onValueChange={setExperimentId}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select experiment" />
                      </SelectTrigger>
                      <SelectContent>
                        {experiments.map((x) => (
                          <SelectItem key={x.experiment_id} value={x.name}>
                            {x.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <AddExperimentDialog onCreated={handleExperimentCreated} />
                </div>
              </div>

              <div className="space-y-2">
                <Label>Driver *</Label>
                <div className="flex items-center gap-2">
                  <div className="flex-1">
                    <Select value={formDriver} onValueChange={setDriver}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select driver" />
                      </SelectTrigger>
                      <SelectContent>
                        {drivers.map((d) => (
                          <SelectItem key={d.driver_id} value={d.name}>
                            {d.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <AddDriverDialog onCreated={handleDriverCreated} />
                </div>
              </div>

              <div className="space-y-2">
                <Label>Mode *</Label>
                <Select
                  value={formMode}
                  onValueChange={(v) => setMode(v as TestMode)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select mode" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="easy">Easy</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="pro">Pro</SelectItem>
                  </SelectContent>
                </Select>
                {!isValid && (
                  <p className="text-sm text-muted-foreground">
                    Required — pick a mode to enable saving.
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="requirements">Requirements</Label>
                <Textarea
                  id="requirements"
                  value={formRequirements}
                  onChange={(e) => setRequirements(e.target.value)}
                  placeholder={`e.g.
The driver shall finish Monza under 55.250s.
The car shall not exceed 3.5G longitudinal.
Tyre temperature shall stay below 80°C.`}
                  disabled={isSubmitting}
                  rows={5}
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button
                  type="submit"
                  disabled={isSubmitting || !isDirty || !isValid}
                  data-testid="save-test"
                >
                  {isSubmitting ? "Saving..." : "Save Changes"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push(`/tests/${testId}`)}
                >
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
