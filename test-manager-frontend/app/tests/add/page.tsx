"use client";

import { useRouter } from "next/navigation";
import { useState, useEffect, useRef } from "react";
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

export default function AddTestPage() {
  const router = useRouter();
  const { toast } = useToast();
  const testsApi = useTestsApi();
  const devicesApi = useDevicesApi();
  const driversApi = useDriversApi();
  const environmentsApi = useEnvironmentsApi();
  const experimentsApi = useExperimentsApi();

  // Bound to the experiment NAME (path A): Test.experiment_id stores the name
  // string verbatim, which is what flows to DCM + the lake partition.
  const [experimentId, setExperimentId] = useState("");
  const [pendingExperiment, setPendingExperiment] = useState<string | null>(
    null,
  );
  const [pcDeviceId, setPcDeviceId] = useState("");
  const [testRigDeviceId, setTestRigDeviceId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [driver, setDriver] = useState("");
  const [pendingDriver, setPendingDriver] = useState<string | null>(null);
  const [requirements, setRequirements] = useState("");
  // Synchronous "user has edited requirements" guard — set in onChange before
  // the prefill fetch can return, so a keystroke always beats the seed.
  const requirementsTouched = useRef(false);
  const [mode, setMode] = useState<TestMode | "">("");
  const [isSubmitting, setIsSubmitting] = useState(false);

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

  // Prefill requirements from the most recent test, but only if the user
  // hasn't started typing by the time the request returns.
  useEffect(() => {
    const prefillRequirements = async () => {
      try {
        const { requirements: last } = await testsApi.getLastRequirements();
        if (last && !requirementsTouched.current) {
          setRequirements(last);
        }
      } catch (error) {
        console.error("Failed to prefill requirements:", error);
      }
    };
    prefillRequirements();
  }, []);

  // Preselect first option once items mount. Must run AFTER the render that
  // mounts the SelectItems, otherwise Radix Select can't match the value to
  // an item and resets via onValueChange(""). Prod-only race observed on
  // cloud; dev worked because StrictMode re-renders masked the timing.
  useEffect(() => {
    if (pcDevices.length > 0 && !pcDeviceId) {
      setPcDeviceId(pcDevices[0].device_id);
    }
  }, [pcDevices, pcDeviceId]);
  useEffect(() => {
    if (testRigDevices.length > 0 && !testRigDeviceId) {
      setTestRigDeviceId(testRigDevices[0].device_id);
    }
  }, [testRigDevices, testRigDeviceId]);
  useEffect(() => {
    if (drivers.length > 0 && !driver) {
      setDriver(drivers[0].name);
    }
  }, [drivers, driver]);
  // Select a just-created driver, but only once the refetched list actually
  // contains its SelectItem — setting the value in the same batch as setDrivers
  // trips the Radix preselect race (onValueChange("") clears it in prod).
  useEffect(() => {
    if (pendingDriver && drivers.some((d) => d.name === pendingDriver)) {
      setDriver(pendingDriver);
      setPendingDriver(null);
    }
  }, [drivers, pendingDriver]);
  useEffect(() => {
    if (environments.length > 0 && !environmentId) {
      setEnvironmentId(environments[0].environment_id);
    }
  }, [environments, environmentId]);
  useEffect(() => {
    if (experiments.length > 0 && !experimentId) {
      setExperimentId(experiments[0].name);
    }
  }, [experiments, experimentId]);
  // Select a just-created experiment once the refetched list contains it (same
  // Radix preselect race as the driver picker).
  useEffect(() => {
    if (
      pendingExperiment &&
      experiments.some((x) => x.name === pendingExperiment)
    ) {
      setExperimentId(pendingExperiment);
      setPendingExperiment(null);
    }
  }, [experiments, pendingExperiment]);

  const refetchDrivers = async () => {
    try {
      const res = await driversApi.list({ page_size: 100 });
      setDrivers(res.items);
    } catch (error) {
      console.error("Failed to refetch drivers:", error);
    }
  };

  const handleDriverCreated = async (created: Driver) => {
    setPendingDriver(created.name);
    await refetchDrivers();
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (
      !experimentId.trim() ||
      !pcDeviceId ||
      !testRigDeviceId ||
      !environmentId ||
      !driver ||
      !mode
    )
      return;

    try {
      setIsSubmitting(true);
      const created = await testsApi.create({
        experiment_id: experimentId.trim(),
        pc_device_id: pcDeviceId,
        test_rig_device_id: testRigDeviceId,
        environment_id: environmentId,
        driver,
        requirements: requirements.trim(),
        mode: mode as TestMode,
      });

      const pcName =
        pcDevices.find((d) => d.device_id === pcDeviceId)?.name ?? pcDeviceId;
      toast({
        title: "Test Created",
        description: `${created.test_id} is now the active config for ${pcName}.`,
      });

      router.push(`/tests/${created.test_id}`);
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error ? error.message : "Failed to create test.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const isFormValid =
    experimentId.trim() &&
    pcDeviceId &&
    testRigDeviceId &&
    environmentId &&
    driver &&
    mode;

  return (
    <MainLayout backLink={{ href: "/tests", label: "Back to Tests" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Create Test</h1>

        <Card>
          <CardHeader>
            <CardTitle>Setup</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label>PC (Hostname) *</Label>
                <Select value={pcDeviceId} onValueChange={setPcDeviceId}>
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
                  value={testRigDeviceId}
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
                <Select value={environmentId} onValueChange={setEnvironmentId}>
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
                      value={experimentId}
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
                    <Select value={driver} onValueChange={setDriver}>
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
                  value={mode}
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
              </div>

              <div className="space-y-2">
                <Label htmlFor="requirements">Requirements</Label>
                <Textarea
                  id="requirements"
                  value={requirements}
                  onChange={(e) => {
                    requirementsTouched.current = true;
                    setRequirements(e.target.value);
                  }}
                  placeholder={`e.g.
The driver shall finish Monza under 55.250s.
The car shall not exceed 3.5G longitudinal.
Tyre temperature shall stay below 80°C.`}
                  disabled={isSubmitting}
                  rows={5}
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !isFormValid}>
                  {isSubmitting ? "Creating..." : "Create Test"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push("/tests")}
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
