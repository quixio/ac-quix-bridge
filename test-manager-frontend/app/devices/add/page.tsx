"use client"

import { useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Button } from "@/components/ui/button"
import { DeviceForm } from "@/components/devices/device-form"
import { useDevicesApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { ArrowLeft } from "lucide-react"
import type { DeviceCreateFormData, DeviceUpdateFormData } from "@/lib/schemas/device-schema"

export default function AddDevicePage() {
  const router = useRouter()
  const { toast } = useToast()
  const devicesApi = useDevicesApi()
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Mock current user - in production, get from auth context
  const currentUser = "current-user" // TODO: Get from auth context

  const handleSubmit = async (data: DeviceCreateFormData | DeviceUpdateFormData) => {
    try {
      setIsSubmitting(true)
      // Type assertion needed due to form union type
      const createdDevice = await devicesApi.create(data as any)

      toast({
        title: "Device Created",
        description: `Device ${createdDevice.device_id} has been created successfully.`,
      })

      // Redirect to the newly created device's detail page
      router.push(`/devices/${createdDevice.device_id}`)
    } catch (error) {
      console.error("Failed to create device:", error)
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to create device. Please try again.",
        variant: "destructive",
      })
      throw error
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCancel = () => {
    router.push("/devices")
  }

  return (
    <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
      <div className="max-w-7xl space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Create New Device</h1>
          <div className="w-24" /> {/* Spacer for center alignment */}
        </div>

        {/* Phase 2.2 Notice */}
        <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
          <p className="text-sm text-blue-900 dark:text-blue-100">
            <strong>Note:</strong> Some dropdowns such as Manufacturer, Product Category, Product Name, and Product Type will be available in Phase 2.2.
          </p>
        </div>

        {/* Device Form */}
        <DeviceForm
          mode="create"
          onSubmit={handleSubmit}
          onCancel={handleCancel}
          currentUser={currentUser}
        />
      </div>
    </MainLayout>
  )
}
