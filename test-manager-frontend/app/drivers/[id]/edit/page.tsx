"use client"

import { useParams, useRouter } from "next/navigation"
import { useState, useEffect } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useDriver } from "@/lib/hooks/use-drivers"
import { useDriversApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"

export default function EditDriverPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const driversApi = useDriversApi()
  const driverId = params.id as string
  const { driver, loading } = useDriver(driverId)
  const [name, setName] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    if (driver) setName(driver.name)
  }, [driver])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return

    try {
      setIsSubmitting(true)
      await driversApi.update(driverId, { name: name.trim() })

      toast({
        title: "Driver Updated",
        description: `Driver ${driverId} has been updated.`,
      })

      router.push(`/drivers/${driverId}`)
    } catch (error) {
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to update driver.",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  if (loading) {
    return (
      <MainLayout backLink={{ href: `/drivers/${driverId}`, label: "Back to Driver" }}>
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    )
  }

  return (
    <MainLayout backLink={{ href: `/drivers/${driverId}`, label: "Back to Driver" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Edit Driver</h1>

        <Card>
          <CardHeader>
            <CardTitle>Driver Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label>Driver ID</Label>
                <Input value={driverId} disabled />
              </div>

              <div className="space-y-2">
                <Label htmlFor="name">Name *</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Enter driver name"
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !name.trim()}>
                  {isSubmitting ? "Saving..." : "Save Changes"}
                </Button>
                <Button type="button" variant="outline" onClick={() => router.push(`/drivers/${driverId}`)}>
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
