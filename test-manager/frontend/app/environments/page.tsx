"use client"

import { MainLayout } from "@/components/layout/main-layout"
import { EnvironmentsTable } from "@/components/environments/environments-table"
import { EnvironmentsFilters } from "@/components/environments/environments-filters"
import { Pagination } from "@/components/shared/pagination"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Plus, AlertTriangle } from "lucide-react"
import { mockEnvironments } from "@/lib/data/mock-environments"
import { useToast } from "@/lib/hooks/use-toast"

export default function EnvironmentsPage() {
  const { toast } = useToast()

  const handleAddEnvironment = () => {
    toast({
      title: "Feature under construction",
      description: "Adding new environments will be available soon.",
    })
  }

  return (
    <MainLayout>
      <div className="max-w-7xl">
        <div className="space-y-6">
        {/* Page Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Environments</h1>
            <p className="text-muted-foreground">
              Manage test benches and testing environments
            </p>
          </div>
          <Button onClick={handleAddEnvironment}>
            <Plus className="mr-2 h-4 w-4" />
            Add Environment
          </Button>
        </div>

        {/* Under Construction Alert */}
        <Alert variant="default" className="border-amber-500/50 bg-amber-500/10 text-amber-900 dark:text-amber-100">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            This feature is currently under development. The data shown below is for demonstration purposes only.
          </AlertDescription>
        </Alert>

        {/* Filters */}
        <EnvironmentsFilters />

        {/* Table */}
        <EnvironmentsTable data={mockEnvironments} />

        {/* Pagination (Mock) */}
        <Pagination
          page={1}
          pageSize={20}
          total={7}
          totalPages={1}
          onPageChange={() => {
            toast({
              title: "Feature under construction",
              description: "Pagination will be functional when this feature is complete.",
            })
          }}
          onPageSizeChange={() => {
            toast({
              title: "Feature under construction",
              description: "Pagination will be functional when this feature is complete.",
            })
          }}
        />

        {/* Footer Note */}
        <div className="rounded-lg border border-border bg-muted/30 p-4">
          <p className="text-sm text-muted-foreground text-center">
            This is a preview of the Environments feature. Full functionality will be available in a future release.
          </p>
        </div>
      </div>
      </div>
    </MainLayout>
  )
}
