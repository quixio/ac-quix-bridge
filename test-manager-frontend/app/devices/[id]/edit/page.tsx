"use client"

import { useParams, useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Button } from "@/components/ui/button"
import { DeviceForm } from "@/components/devices/device-form"
import { JournalMetadataDialog } from "@/components/devices/journal-metadata-dialog"
import { useDevicesApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { Skeleton } from "@/components/ui/skeleton"
import { ArrowLeft } from "lucide-react"
import type { Device, DeviceUpdatePreview } from "@/types/device"
import type { DeviceCreateFormData, DeviceUpdateFormData } from "@/lib/schemas/device-schema"

export default function EditDevicePage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const devicesApi = useDevicesApi()
  const deviceId = params.id as string

  const [device, setDevice] = useState<Device | null>(null)
  const [loading, setLoading] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Preview dialog state
  const [previewDialogOpen, setPreviewDialogOpen] = useState(false)
  const [previewData, setPreviewData] = useState<DeviceUpdatePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [pendingFormData, setPendingFormData] = useState<DeviceUpdateFormData | null>(null)
  const [newDeviceData, setNewDeviceData] = useState<Device | null>(null)

  // Mock current user - in production, get from auth context
  const currentUser = "current-user" // TODO: Get from auth context

  // Fetch device data on mount
  useEffect(() => {
    const fetchDevice = async () => {
      try {
        setLoading(true)
        const data = await devicesApi.get(deviceId)
        setDevice(data)
      } catch (error) {
        console.error("Failed to fetch device:", error)
        toast({
          title: "Error",
          description: "Failed to load device data. Please try again.",
          variant: "destructive",
        })
      } finally {
        setLoading(false)
      }
    }

    fetchDevice()
  }, [deviceId, toast])

  // Step 1: Form submit -> fetch preview from backend
  const handleSubmit = async (data: DeviceCreateFormData | DeviceUpdateFormData) => {
    if (!device) return

    try {
      setPreviewLoading(true)
      setPendingFormData(data as DeviceUpdateFormData)

      // Fetch preview from backend (without journal_text)
      const { journal_text, ...dataWithoutJournalText } = data
      const preview = await devicesApi.preview(deviceId, dataWithoutJournalText)

      // Merge form data with original device to create newData for diff
      const mergedData: Device = {
        ...device,
        ...dataWithoutJournalText,
        // Update sample_id if sample_type or sample_nr changed
        sample_id: dataWithoutJournalText.sample_type
          ? dataWithoutJournalText.sample_nr
            ? `${dataWithoutJournalText.sample_type}-${dataWithoutJournalText.sample_nr}`
            : dataWithoutJournalText.sample_type
          : device.sample_id,
      }

      setPreviewData(preview)
      setNewDeviceData(mergedData)
      setPreviewDialogOpen(true)
    } catch (error) {
      console.error("Failed to fetch preview:", error)
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to load preview. Please try again.",
        variant: "destructive",
      })
    } finally {
      setPreviewLoading(false)
    }
  }

  // Step 2: User confirms in preview dialog -> actually save with journal text
  const handleConfirmSave = async (journalText: string) => {
    if (!pendingFormData) return

    try {
      setIsSubmitting(true)

      // Add journal text to the pending form data
      const finalData = {
        ...pendingFormData,
        journal_text: journalText,
      }

      const updatedDevice = await devicesApi.update(deviceId, finalData)

      toast({
        title: "Device Updated",
        description: `Device ${updatedDevice.device_id} has been updated successfully.`,
      })

      // Close dialog and redirect
      setPreviewDialogOpen(false)
      router.push(`/devices/${deviceId}`)
    } catch (error) {
      console.error("Failed to update device:", error)
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to update device. Please try again.",
        variant: "destructive",
      })
      throw error
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCancel = () => {
    router.push(`/devices/${deviceId}`)
  }

  if (loading) {
    return (
      <MainLayout backLink={{ href: `/devices/${deviceId}`, label: "Back to Device" }}>
        <div className="max-w-7xl space-y-6">
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-bold">Edit Device</h1>
            <div className="w-24" />
          </div>

          {/* Loading skeletons */}
          <div className="space-y-6">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        </div>
      </MainLayout>
    )
  }

  if (!device) {
    return (
      <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
        <div className="max-w-7xl space-y-6">
          <div className="text-center py-12">
            <p className="text-lg text-muted-foreground">Device not found</p>
          </div>
        </div>
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: `/devices/${deviceId}`, label: "Back to Device" }}>
      <div className="max-w-7xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Edit Device: {deviceId}</h1>
        <div className="w-24" /> {/* Spacer for center alignment */}
      </div>

      {/* Phase 2.2 Notice */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <p className="text-sm text-blue-900 dark:text-blue-100">
          <strong>Note:</strong> Some dropdowns such as Manufacturer, Product Category, Product Name, and Product Type will be available for editing in Phase 2.2.
        </p>
      </div>

      {/* Device Form */}
      <DeviceForm
        mode="edit"
        device={device}
        onSubmit={handleSubmit}
        onCancel={handleCancel}
        currentUser={currentUser}
      />

      {/* Journal Metadata Dialog (with preview) */}
      {device && newDeviceData && (
        <JournalMetadataDialog
          open={previewDialogOpen}
          onOpenChange={setPreviewDialogOpen}
          originalData={device}
          newData={newDeviceData}
          preview={previewData}
          loading={previewLoading}
          onConfirm={handleConfirmSave}
        />
      )}
      </div>
    </MainLayout>
  )
}
