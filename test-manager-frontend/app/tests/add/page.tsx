"use client";

import { useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
} from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { DeviceCategory } from "@/types/device";
import type { Device } from "@/types/device";
import type { Driver } from "@/types/driver";
import type { Environment } from "@/types/environment";
import type { TestMode } from "@/types/test";

export default function AddTestPage() {
  const router = useRouter();
  const { toast } = useToast();
  const testsApi = useTestsApi();
  const devicesApi = useDevicesApi();
  const driversApi = useDriversApi();
  const environmentsApi = useEnvironmentsApi();

  const [experimentId, setExperimentId] = useState("");
  const [pcDeviceId, setPcDeviceId] = useState("");
  const [testRigDeviceId, setTestRigDeviceId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [driver, setDriver] = useState("");
  const [requirements, setRequirements] = useState("");
  const [mode, setMode] = useState<TestMode | "">("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Dropdown data
  const [pcDevices, setPcDevices] = useState<Device[]>([]);
  const [testRigDevices, setTestRigDevices] = useState<Device[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [pcRes, rigRes, drvRes, envRes] = await Promise.all([
          devicesApi.list({ category: DeviceCategory.PC, page_size: 100 }),
          devicesApi.list({
            category: DeviceCategory.TEST_RIG,
            page_size: 100,
          }),
          driversApi.list({ page_size: 100 }),
          environmentsApi.list({ page_size: 100 }),
        ]);
        setPcDevices(pcRes.items);
        setTestRigDevices(rigRes.items);
        setDrivers(drvRes.items);
        setEnvironments(envRes.items);
      } catch (error) {
        console.error("Failed to fetch dropdown data:", error);
      }
    };
    fetchData();
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
  useEffect(() => {
    if (environments.length > 0 && !environmentId) {
      setEnvironmentId(environments[0].environment_id);
    }
  }, [environments, environmentId]);

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

      toast({
        title: "Test Created",
        description: `Test ${created.test_id} has been created.`,
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
                <Label htmlFor="experiment_id">Experiment *</Label>
                <Input
                  id="experiment_id"
                  value={experimentId}
                  onChange={(e) => setExperimentId(e.target.value)}
                  placeholder="e.g. tyre_pressure_comparison"
                  disabled={isSubmitting}
                />
              </div>

              <div className="space-y-2">
                <Label>Driver *</Label>
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
