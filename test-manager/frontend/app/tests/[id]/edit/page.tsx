"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { MainLayout } from "@/components/layout/main-layout"
import { TestForm } from "@/components/tests/test-form"
import { useTestsApi } from "@/lib/hooks/use-api"
import { useTest } from "@/lib/hooks/use-tests"
import { useToast } from "@/lib/hooks/use-toast"
import { TestCreateInput } from "@/lib/schemas/test-schema"
import { ArrowLeft, FileText } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/shared/empty-state"

export default function EditTestPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const testsApi = useTestsApi()
  const testId = params.id as string

  const { test, loading, error, refetch } = useTest(testId)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (data: TestCreateInput) => {
    setIsSubmitting(true)

    try {
      // Transform Date objects to ISO strings for backend
      const payload = {
        ...data,
        start: data.start instanceof Date ? data.start.toISOString() : data.start,
        end: data.end instanceof Date ? data.end.toISOString() : data.end,
      }

      const updated = await testsApi.update(testId, payload as any)

      toast({
        title: "Test updated",
        description: `Test ${updated.test_id} has been updated successfully.`,
      })

      // Navigate back to detail page
      router.push(`/tests/${testId}`)
    } catch (error) {
      toast({
        title: "Error updating test",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
      setIsSubmitting(false)
    }
  }

  const handleCancel = () => {
    router.push(`/tests/${testId}`)
  }

  if (loading) {
    return (
      <MainLayout backLink={{ href: `/tests/${testId}`, label: "Back to Test Detail" }}>
        <div className="max-w-4xl space-y-6">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </MainLayout>
    )
  }

  if (error || !test) {
    return (
      <MainLayout backLink={{ href: `/tests/${testId}`, label: "Back to Test Detail" }}>
        <div className="max-w-4xl">
          <EmptyState
            icon={<FileText className="h-12 w-12" />}
            title="Failed to load test"
            description={error?.message || "Test not found"}
            action={{
              label: "Retry",
              onClick: refetch,
            }}
          />
        </div>
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: `/tests/${testId}`, label: "Back to Test Detail" }}>
      <div className="max-w-4xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">Edit Test: {test.test_id}</h1>
        <p className="text-muted-foreground mt-2">
          Update test execution details
        </p>
      </div>

      {/* Form */}
      <div className="bg-card border rounded-lg p-6">
        <TestForm
          initialData={test}
          onSubmit={handleSubmit}
          onCancel={handleCancel}
          isSubmitting={isSubmitting}
        />
      </div>
      </div>
    </MainLayout>
  )
}
