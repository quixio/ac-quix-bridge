"use client"

import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useDevice } from "@/lib/hooks/use-devices"
import { useDevicesApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { DeviceCategory, DeviceCategoryLabels, DeviceStatus } from "@/types/device"

export default function EditDevicePage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const devicesApi = useDevicesApi()
  const deviceId = params.id as string
  const { device, loading } = useDevice(deviceId)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [name, setName] = useState<string | null>(null)
  const [category, setCategory] = useState<DeviceCategory | null>(null)
  const [status, setStatus] = useState<DeviceStatus | null>(null)

  const formName = name ?? device?.name ?? ""
  const formCategory = category ?? device?.category ?? DeviceCategory.PC
  const formStatus = status ?? device?.status ?? DeviceStatus.ACTIVE

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formName.trim()) return

    try {
      setIsSubmitting(true)
      await devicesApi.update(deviceId, {
        name: formName.trim(),
        category: formCategory,
        status: formStatus,
      })

      toast({
        title: "Device Updated",
        description: `Device ${deviceId} has been updated.`,
      })

      router.push(`/devices/${deviceId}`)
    } catch (error) {
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to update device.",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  if (loading || !device) {
    return (
      <MainLayout backLink={{ href: `/devices/${deviceId}`, label: "Back to Device" }}>
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: `/devices/${deviceId}`, label: "Back to Device" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Edit Device</h1>

        <Card>
          <CardHeader>
            <CardTitle>Device Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label>Device ID</Label>
                <Input value={deviceId} disabled />
              </div>

              <div className="space-y-2">
                <Label>Category *</Label>
                <Select value={formCategory} onValueChange={(v) => setCategory(v as DeviceCategory)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={DeviceCategory.PC}>{DeviceCategoryLabels[DeviceCategory.PC]}</SelectItem>
                    <SelectItem value={DeviceCategory.TEST_RIG}>{DeviceCategoryLabels[DeviceCategory.TEST_RIG]}</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="name">Name *</Label>
                <Input
                  id="name"
                  value={formName}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. XPS, Logitech G29"
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="space-y-2">
                <Label>Status</Label>
                <Select value={formStatus} onValueChange={(v) => setStatus(v as DeviceStatus)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={DeviceStatus.ACTIVE}>Active</SelectItem>
                    <SelectItem value={DeviceStatus.INACTIVE}>Inactive</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !formName.trim()}>
                  {isSubmitting ? "Saving..." : "Save Changes"}
                </Button>
                <Button type="button" variant="outline" onClick={() => router.push(`/devices/${deviceId}`)}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  )
}
