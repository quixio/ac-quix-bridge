"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { MainLayout } from "@/components/layout/main-layout"
import { TestForm } from "@/components/tests/test-form"
import { useTestsApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"
import { TestCreateInput } from "@/lib/schemas/test-schema"
import { ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"

export default function CreateTestPage() {
  const router = useRouter()
  const { toast } = useToast()
  const testsApi = useTestsApi()
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

      const test = await testsApi.create(payload as any)

      toast({
        title: "Test created",
        description: `Test ${test.test_id} has been created successfully.`,
      })

      // Navigate to the test detail page
      router.push(`/tests/${test.test_id}`)
    } catch (error) {
      toast({
        title: "Error creating test",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
      setIsSubmitting(false)
    }
  }

  const handleCancel = () => {
    router.push("/tests")
  }

  return (
    <MainLayout backLink={{ href: "/tests", label: "Back to Tests" }}>
      <div className="max-w-4xl">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold tracking-tight">Create New Test</h1>
          <p className="text-muted-foreground mt-2">
            Fill in the details to create a new test execution
          </p>
        </div>

        {/* Form */}
        <div className="bg-card border rounded-lg p-6">
          <TestForm
            onSubmit={handleSubmit}
            onCancel={handleCancel}
            isSubmitting={isSubmitting}
          />
        </div>
      </div>
    </MainLayout>
  )
}
