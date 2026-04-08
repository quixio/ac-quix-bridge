"use client"

import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Button } from "@/components/ui/button"
import { NavigationButton } from "@/components/ui/navigation-button"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { DeviceDetailCard } from "@/components/devices/device-detail-card"
import { JournalTimeline } from "@/components/devices/journal-timeline"
import { JournalEntryFormDialog } from "@/components/devices/journal-entry-form-dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useDevice, useDeviceJournal } from "@/lib/hooks/use-devices"
import { useDevicesApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { ArrowLeft, Edit, Trash2, Package, Plus } from "lucide-react"
import type { JournalEntryCreateFormData } from "@/lib/schemas/device-schema"

export default function DeviceDetailPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const devicesApi = useDevicesApi()
  const deviceId = params.id as string

  // State for journal entry dialog
  const [journalDialogOpen, setJournalDialogOpen] = useState(false)
  // State for delete confirmation
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  // Mock current user - in production, get from auth context
  const currentUser = "current-user" // TODO: Get from auth context

  // Fetch device and journal
  const { device, loading: deviceLoading, error: deviceError } = useDevice(deviceId)
  const {
    journal,
    loading: journalLoading,
    error: journalError,
    refetch: refetchJournal,
  } = useDeviceJournal(deviceId)

  // Handle manual journal entry creation
  const handleCreateJournalEntry = async (data: JournalEntryCreateFormData) => {
    try {
      // Add editor field to the request
      await devicesApi.createJournalEntry(deviceId, {
        ...data,
        editor: currentUser,
      })
      toast({
        title: "Journal Entry Added",
        description: "The journal entry has been created successfully.",
      })
      // Refresh journal timeline
      refetchJournal()
    } catch (error) {
      console.error("Failed to create journal entry:", error)
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to create journal entry.",
        variant: "destructive",
      })
      throw error
    }
  }

  // Handle device deletion
  const handleDeleteDevice = async () => {
    setIsDeleting(true)
    try {
      await devicesApi.delete(deviceId)
      toast({
        title: "Device Deleted",
        description: `Device ${deviceId} has been permanently deleted.`,
      })
      // Navigate back to devices list
      router.push("/devices")
    } catch (error) {
      console.error("Failed to delete device:", error)

      // Extract error message (handle both API error format and generic errors)
      let errorMessage = "Failed to delete device."
      if (error && typeof error === "object" && "message" in error) {
        errorMessage = (error as Error).message
      }

      toast({
        title: "Cannot Delete Device",
        description: errorMessage,
        variant: "destructive",
      })
      setIsDeleting(false)
      setDeleteDialogOpen(false)
    }
  }

  // Loading state
  if (deviceLoading) {
    return (
      <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
        <div className="max-w-7xl space-y-6">
          <Skeleton className="h-10 w-64" />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Skeleton className="h-96 w-full" />
            <Skeleton className="h-96 w-full" />
          </div>
        </div>
      </MainLayout>
    )
  }

  // Error state
  if (deviceError || !device) {
    return (
      <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
        <div className="max-w-7xl">
          <EmptyState
            icon={<Package className="h-12 w-12" />}
            title="Device not found"
            description={
              deviceError?.message ||
              "The device you're looking for doesn't exist or has been deleted."
            }
            action={{
              label: "Back to Devices",
              onClick: () => router.push("/devices"),
            }}
          />
        </div>
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
      <div className="max-w-7xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-bold tracking-tight">
            {device.device_id}
          </h1>
          <p className="text-muted-foreground">
            {device.product_name} - {device.sample_id}
          </p>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <NavigationButton
            variant="outline"
            href={`/devices/${deviceId}/edit`}
          >
            <Edit className="mr-2 h-4 w-4" />
            Edit Device
          </NavigationButton>
          <Button
            variant="destructive"
            onClick={() => setDeleteDialogOpen(true)}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Delete Device
          </Button>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left column: Device details */}
        <div className="space-y-6">
          <DeviceDetailCard device={device} />
        </div>

        {/* Right column: Journal timeline */}
        <div>
          <JournalTimeline
            entries={journal}
            loading={journalLoading}
            error={journalError}
            headerAction={
              <Button variant="outline" onClick={() => setJournalDialogOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                Add Manual Entry
              </Button>
            }
          />
        </div>
      </div>

      {/* Journal Entry Dialog */}
      <JournalEntryFormDialog
        open={journalDialogOpen}
        onOpenChange={setJournalDialogOpen}
        onSubmit={handleCreateJournalEntry}
      />

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Device?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete device <strong>{deviceId}</strong>?
              This action cannot be undone. This will permanently delete the device
              and all associated journal entries.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteDevice}
              disabled={isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      </div>
    </MainLayout>
  )
}
