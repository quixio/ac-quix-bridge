"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FileText, Box, PlusCircle, Users, Server } from "lucide-react";
import {
  useTestsApi,
  useDevicesApi,
  useDriversApi,
  useEnvironmentsApi,
} from "@/lib/hooks/use-api";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import type { Test } from "@/types/test";
import type { Device } from "@/types/device";

export default function Home() {
  const { formatDate } = useDateFormatter();
  const testsApi = useTestsApi();
  const devicesApi = useDevicesApi();
  const driversApi = useDriversApi();
  const environmentsApi = useEnvironmentsApi();
  const [stats, setStats] = useState({
    totalTests: 0,
    totalDevices: 0,
    totalDrivers: 0,
    totalEnvironments: 0,
  });
  const [recentTests, setRecentTests] = useState<Test[]>([]);
  const [recentDevices, setRecentDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchHomeData = async () => {
    try {
      setLoading(true);
      const [
        testsResponse,
        devicesResponse,
        driversResponse,
        environmentsResponse,
      ] = await Promise.all([
        testsApi.list({ page_size: 200 }),
        devicesApi.list({ page_size: 200 }),
        driversApi.list({ page_size: 10 }),
        environmentsApi.list({ page_size: 10 }),
      ]);

      const tests = testsResponse.items;
      const devices = devicesResponse.items;

      setStats({
        totalTests: testsResponse.total,
        totalDevices: devicesResponse.total,
        totalDrivers: driversResponse.total,
        totalEnvironments: environmentsResponse.total,
      });

      const sortedTests = [...tests].sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );
      const sortedDevices = [...devices].sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );

      setRecentTests(sortedTests.slice(0, 5));
      setRecentDevices(sortedDevices.slice(0, 5));
    } catch (error) {
      console.error("Failed to fetch home data:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHomeData();
  }, []);

  const isEmpty =
    stats.totalTests === 0 && stats.totalDevices === 0 && !loading;

  return (
    <MainLayout>
      <div className="max-w-7xl">
        {isEmpty ? (
          <div className="mb-8">
            <div className="rounded-lg border bg-card p-8 text-center">
              <h1 className="text-4xl font-bold tracking-tight mb-3">
                Welcome to Test Manager
              </h1>
              <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
                A comprehensive test execution and device management system
                designed for test engineering teams.
              </p>
            </div>
          </div>
        ) : (
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">Home</h1>
              <p className="text-muted-foreground">
                Overview of tests and devices
              </p>
            </div>
            <div className="flex gap-2">
              <Link href="/tests/add">
                <Button>
                  <PlusCircle className="mr-2 h-4 w-4" />
                  Create Test
                </Button>
              </Link>
              <Link href="/devices/add">
                <Button variant="outline">
                  <PlusCircle className="mr-2 h-4 w-4" />
                  Create Device
                </Button>
              </Link>
            </div>
          </div>
        )}

        <div className="space-y-6">
          {/* Statistics Cards */}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Tests</CardTitle>
                <FileText className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {loading ? "..." : stats.totalTests}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Devices</CardTitle>
                <Box className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {loading ? "..." : stats.totalDevices}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Drivers</CardTitle>
                <Users className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {loading ? "..." : stats.totalDrivers}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">
                  Environments
                </CardTitle>
                <Server className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {loading ? "..." : stats.totalEnvironments}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Recent Activity */}
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Recent Tests</span>
                  <Link href="/tests">
                    <Button variant="ghost" size="sm">
                      View All
                    </Button>
                  </Link>
                </CardTitle>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    <div className="h-16 bg-muted animate-pulse rounded" />
                    <div className="h-16 bg-muted animate-pulse rounded" />
                    <div className="h-16 bg-muted animate-pulse rounded" />
                  </div>
                ) : recentTests.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <FileText className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    <p>No tests yet</p>
                    <Link href="/tests/add">
                      <Button variant="outline" size="sm" className="mt-2">
                        Create your first test
                      </Button>
                    </Link>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {recentTests.map((test) => (
                      <Link
                        key={test.test_id}
                        href={`/tests/${test.test_id}`}
                        className="block"
                      >
                        <div className="rounded-lg border p-3 hover:bg-accent transition-colors">
                          <div className="flex items-center justify-between mb-1">
                            <span className="font-medium text-sm">
                              {test.test_id}
                            </span>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            Experiment: {test.experiment_id}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            Created: {formatDate(test.created_at)}
                          </div>
                        </div>
                      </Link>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Recent Devices</span>
                  <Link href="/devices">
                    <Button variant="ghost" size="sm">
                      View All
                    </Button>
                  </Link>
                </CardTitle>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    <div className="h-16 bg-muted animate-pulse rounded" />
                    <div className="h-16 bg-muted animate-pulse rounded" />
                    <div className="h-16 bg-muted animate-pulse rounded" />
                  </div>
                ) : recentDevices.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <Box className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    <p>No devices yet</p>
                    <Link href="/devices/add">
                      <Button variant="outline" size="sm" className="mt-2">
                        Create your first device
                      </Button>
                    </Link>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {recentDevices.map((device) => (
                      <Link
                        key={device.device_id}
                        href={`/devices/${device.device_id}`}
                        className="block"
                      >
                        <div className="rounded-lg border p-3 hover:bg-accent transition-colors">
                          <div className="flex items-center justify-between mb-1">
                            <span className="font-medium text-sm">
                              {device.device_id}
                            </span>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {device.name}
                          </div>
                        </div>
                      </Link>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </MainLayout>
  );
}
