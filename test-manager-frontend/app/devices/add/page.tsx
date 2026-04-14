"use client"

import { useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useDevicesApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { DeviceCategory, DeviceCategoryLabels } from "@/types/device"

export default function AddDevicePage() {
  const router = useRouter()
  const { toast } = useToast()
  const devicesApi = useDevicesApi()
  const [name, setName] = useState("")
  const [category, setCategory] = useState<DeviceCategory | "">("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !category) return

    try {
      setIsSubmitting(true)
      const created = await devicesApi.create({
        name: name.trim(),
        category: category as DeviceCategory,
      })

      toast({
        title: "Device Created",
        description: `Device ${created.name} (${created.device_id}) has been created.`,
      })

      router.push(`/devices/${created.device_id}`)
    } catch (error) {
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to create device.",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Add Device</h1>

        <Card>
          <CardHeader>
            <CardTitle>Device Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label>Category *</Label>
                <Select value={category} onValueChange={(v) => setCategory(v as DeviceCategory)}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select category" />
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
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={category === DeviceCategory.PC ? "e.g. XPS, patrickpc" : "e.g. Logitech G29, Fanatec DD Pro"}
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !name.trim() || !category}>
                  {isSubmitting ? "Creating..." : "Create Device"}
                </Button>
                <Button type="button" variant="outline" onClick={() => router.push("/devices")}>
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
