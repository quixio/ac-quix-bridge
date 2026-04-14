"use client"

import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { useDriver } from "@/lib/hooks/use-drivers"
import { useDriversApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import { Pencil, Trash2, Users } from "lucide-react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

export default function DriverDetailPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const driversApi = useDriversApi()
  const driverId = params.id as string
  const { driver, loading, error } = useDriver(driverId)
  const { formatDate } = useDateFormatter()
  const [isDeleting, setIsDeleting] = useState(false)

  const handleDelete = async () => {
    try {
      setIsDeleting(true)
      await driversApi.delete(driverId)
      toast({
        title: "Driver Deleted",
        description: `Driver ${driverId} has been deleted.`,
      })
      router.push("/drivers")
    } catch (error) {
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to delete driver.",
        variant: "destructive",
      })
    } finally {
      setIsDeleting(false)
    }
  }

  if (loading) {
    return (
      <MainLayout backLink={{ href: "/drivers", label: "Back to Drivers" }}>
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    )
  }

  if (error || !driver) {
    return (
      <MainLayout backLink={{ href: "/drivers", label: "Back to Drivers" }}>
        <EmptyState
          icon={<Users className="h-12 w-12" />}
          title="Driver not found"
          description={error?.message || "The requested driver could not be found."}
          action={{ label: "Back to Drivers", onClick: () => router.push("/drivers") }}
        />
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: "/drivers", label: "Back to Drivers" }}>
      <div className="max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">{driver.name}</h1>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => router.push(`/drivers/${driverId}/edit`)}>
              <Pencil className="mr-2 h-4 w-4" />
              Edit
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm" disabled={isDeleting}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete Driver</AlertDialogTitle>
                  <AlertDialogDescription>
                    Are you sure you want to delete {driver.name} ({driverId})? This action cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={handleDelete}>Delete</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Driver Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Driver ID</p>
                <p className="font-medium">{driver.driver_id}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Name</p>
                <p className="font-medium">{driver.name}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Created</p>
                <p className="font-medium">{formatDate(driver.created_at)}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Updated</p>
                <p className="font-medium">{formatDate(driver.updated_at)}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  )
}
