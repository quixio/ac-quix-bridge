"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { FileText, Box, PlusCircle, CheckCircle, Clock, Database } from "lucide-react"
import { useTestsApi, useDevicesApi } from "@/lib/hooks/use-api"
import { TestStatusBadge } from "@/components/tests/test-status-badge"
import { DeviceStatusBadge } from "@/components/devices/device-status-badge"
import { SeedDataDialog } from "@/components/admin/seed-data-dialog"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import type { Test } from "@/types/test"
import type { Device } from "@/types/device"

export default function Home() {
  const { formatDate } = useDateFormatter()
  const testsApi = useTestsApi()
  const devicesApi = useDevicesApi()
  const [stats, setStats] = useState({
    totalTests: 0,
    draftTests: 0,
    inProgressTests: 0,
    finishedTests: 0,
    totalDevices: 0,
    activeDevices: 0,
    inactiveDevices: 0,
    retiredDevices: 0,
  })
  const [recentTests, setRecentTests] = useState<Test[]>([])
  const [recentDevices, setRecentDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [showSeedDialog, setShowSeedDialog] = useState(false)

  const fetchHomeData = async () => {
    try {
      setLoading(true)
      // Fetch items with maximum page size to get accurate counts
      // Note: Backend allows page_size values: [10, 20, 50, 100, 200]
      const [testsResponse, devicesResponse] = await Promise.all([
        testsApi.list({ page_size: 200 }), // Fetch up to 200 items per request
        devicesApi.list({ page_size: 200 }),
      ])

      const tests = testsResponse.items
      const devices = devicesResponse.items

      // Use total from API response for accurate count
      const testStats = {
        totalTests: testsResponse.total,
        draftTests: tests.filter((t) => t.status === "draft").length,
        inProgressTests: tests.filter((t) => t.status === "in_progress").length,
        finishedTests: tests.filter((t) => t.status === "finished").length,
      }

      const deviceStats = {
        totalDevices: devicesResponse.total,
        activeDevices: devices.filter((d) => d.status === "setup").length,
        inactiveDevices: devices.filter((d) => d.status === "stored").length,
        retiredDevices: devices.filter((d) => d.status === "scrapped").length,
      }

      setStats({ ...testStats, ...deviceStats })

      // Sort by created_at descending and take the 5 most recent
      const sortedTests = [...tests].sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
      const sortedDevices = [...devices].sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )

      setRecentTests(sortedTests.slice(0, 5))
      setRecentDevices(sortedDevices.slice(0, 5))
    } catch (error) {
      console.error("Failed to fetch home data:", error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchHomeData()
  }, [])

  const isEmpty = stats.totalTests === 0 && stats.totalDevices === 0 && !loading

  return (
    <MainLayout>
      <div className="max-w-7xl">
        {/* Welcome Section */}
        {isEmpty ? (
          <div className="mb-8">
            <div className="rounded-lg border bg-card p-8 text-center">
              <h1 className="text-4xl font-bold tracking-tight mb-3">
                Welcome to Test Manager
              </h1>
              <p className="text-lg text-muted-foreground mb-6 max-w-2xl mx-auto">
                A comprehensive test execution and device management system designed
                for test engineering teams.
              </p>

              {/* Feature Grid */}
              <div className="grid gap-4 md:grid-cols-3 mb-8 text-left">
                <div className="rounded-lg border bg-background p-4">
                  <FileText className="h-8 w-8 mb-3 text-primary" />
                  <h3 className="font-semibold mb-2">Manage Tests</h3>
                  <p className="text-sm text-muted-foreground">
                    Create and track test campaigns with complete traceability through
                    draft, in-progress, and finished states.
                  </p>
                </div>

                <div className="rounded-lg border bg-background p-4">
                  <Box className="h-8 w-8 mb-3 text-primary" />
                  <h3 className="font-semibold mb-2">Track Devices</h3>
                  <p className="text-sm text-muted-foreground">
                    Maintain detailed device records with versioning
                    and complete audit trails through journal entries.
                  </p>
                </div>

                <div className="rounded-lg border bg-background p-4">
                  <CheckCircle className="h-8 w-8 mb-3 text-primary" />
                  <h3 className="font-semibold mb-2">Ensure Quality</h3>
                  <p className="text-sm text-muted-foreground">
                    Link multiple devices to tests, track safety requirements,
                    and maintain comprehensive documentation for compliance.
                  </p>
                </div>
              </div>

              {/* Quick Actions */}
              <div className="grid gap-4 md:grid-cols-3 mb-8">
                <div className="rounded-lg border bg-background p-6 text-center">
                  <Database className="h-10 w-10 mb-4 text-primary mx-auto" />
                  <h3 className="font-semibold mb-2">Generate Test Data</h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    Generate realistic sample data with devices, tests, journals, and logbook entries
                  </p>
                  <Button
                    onClick={() => setShowSeedDialog(true)}
                    variant="secondary"
                    className="w-full"
                  >
                    <Database className="mr-2 h-4 w-4" />
                    Generate Data
                  </Button>
                </div>

                <div className="rounded-lg border bg-background p-6 text-center">
                  <Box className="h-10 w-10 mb-4 text-primary mx-auto" />
                  <h3 className="font-semibold mb-2">Create Your First Device</h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    Add a new device under test to start tracking and testing
                  </p>
                  <Link href="/devices/add">
                    <Button variant="default" className="w-full">
                      <PlusCircle className="mr-2 h-4 w-4" />
                      Create Device
                    </Button>
                  </Link>
                </div>

                <div className="rounded-lg border bg-background p-6 text-center">
                  <FileText className="h-10 w-10 mb-4 text-primary mx-auto" />
                  <h3 className="font-semibold mb-2">Create Your First Test</h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    Create a new test campaign to begin your testing workflow
                  </p>
                  <Link href="/tests/add">
                    <Button variant="outline" className="w-full">
                      <PlusCircle className="mr-2 h-4 w-4" />
                      Create Test
                    </Button>
                  </Link>
                </div>
              </div>
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
              <CardTitle className="text-sm font-medium">Total Tests</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{loading ? "..." : stats.totalTests}</div>
              <p className="text-xs text-muted-foreground mt-1">
                {stats.inProgressTests} in progress
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Draft Tests</CardTitle>
              <Clock className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{loading ? "..." : stats.draftTests}</div>
              <p className="text-xs text-muted-foreground mt-1">Ready to start</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Finished Tests</CardTitle>
              <CheckCircle className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{loading ? "..." : stats.finishedTests}</div>
              <p className="text-xs text-muted-foreground mt-1">Completed</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total Devices</CardTitle>
              <Box className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{loading ? "..." : stats.totalDevices}</div>
              <p className="text-xs text-muted-foreground mt-1">{stats.activeDevices} active</p>
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
                  <Button variant="ghost" size="sm">View All</Button>
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
                    <Link key={test.test_id} href={`/tests/${test.test_id}`} className="block">
                      <div className="rounded-lg border p-3 hover:bg-accent transition-colors">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium text-sm">{test.test_id}</span>
                          <TestStatusBadge status={test.status} />
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
                  <Button variant="ghost" size="sm">View All</Button>
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
                    <Link key={device.device_id} href={`/devices/${device.device_id}`} className="block">
                      <div className="rounded-lg border p-3 hover:bg-accent transition-colors">
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-medium text-sm">{device.device_id}</span>
                          <DeviceStatusBadge status={device.status} />
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

      {/* Seed Data Dialog */}
      <SeedDataDialog
        open={showSeedDialog}
        onOpenChange={setShowSeedDialog}
        onSuccess={fetchHomeData}
      />
    </MainLayout>
  )
}
