"use client"

import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"
import { EnvironmentStatusBadge } from "@/components/environments/environment-status-badge"
import { useEnvironment } from "@/lib/hooks/use-environments"
import { useEnvironmentsApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { useDateFormatter } from "@/lib/hooks/use-date-formatter"
import { Pencil, Trash2, Server } from "lucide-react"
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

export default function EnvironmentDetailPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const environmentsApi = useEnvironmentsApi()
  const environmentId = params.id as string
  const { environment, loading, error } = useEnvironment(environmentId)
  const { formatDate } = useDateFormatter()
  const [isDeleting, setIsDeleting] = useState(false)

  const handleDelete = async () => {
    try {
      setIsDeleting(true)
      await environmentsApi.delete(environmentId)
      toast({
        title: "Environment Deleted",
        description: `Environment ${environmentId} has been deleted.`,
      })
      router.push("/environments")
    } catch (error) {
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to delete environment.",
        variant: "destructive",
      })
    } finally {
      setIsDeleting(false)
    }
  }

  if (loading) {
    return (
      <MainLayout backLink={{ href: "/environments", label: "Back to Environments" }}>
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    )
  }

  if (error || !environment) {
    return (
      <MainLayout backLink={{ href: "/environments", label: "Back to Environments" }}>
        <EmptyState
          icon={<Server className="h-12 w-12" />}
          title="Environment not found"
          description={error?.message || "The requested environment could not be found."}
          action={{ label: "Back to Environments", onClick: () => router.push("/environments") }}
        />
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: "/environments", label: "Back to Environments" }}>
      <div className="max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{environment.name}</h1>
            <EnvironmentStatusBadge status={environment.status} />
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => router.push(`/environments/${environmentId}/edit`)}>
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
                  <AlertDialogTitle>Delete Environment</AlertDialogTitle>
                  <AlertDialogDescription>
                    Are you sure you want to delete {environment.name} ({environmentId})? This action cannot be undone.
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
            <CardTitle>Environment Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Environment ID</p>
                <p className="font-medium">{environment.environment_id}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Name</p>
                <p className="font-medium">{environment.name}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Location</p>
                <p className="font-medium">{environment.location || "—"}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Status</p>
                <EnvironmentStatusBadge status={environment.status} />
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Created</p>
                <p className="font-medium">{formatDate(environment.created_at)}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Updated</p>
                <p className="font-medium">{formatDate(environment.updated_at)}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  )
}
