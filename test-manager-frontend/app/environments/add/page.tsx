"use client"

import { useRouter } from "next/navigation"
import { useState } from "react"
import { MainLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useEnvironmentsApi } from "@/lib/hooks/use-api"
import { useToast } from "@/lib/hooks/use-toast"

export default function AddEnvironmentPage() {
  const router = useRouter()
  const { toast } = useToast()
  const environmentsApi = useEnvironmentsApi()
  const [name, setName] = useState("")
  const [location, setLocation] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return

    try {
      setIsSubmitting(true)
      const created = await environmentsApi.create({
        name: name.trim(),
        location: location.trim() || undefined,
      })

      toast({
        title: "Environment Created",
        description: `Environment ${created.name} (${created.environment_id}) has been created.`,
      })

      router.push(`/environments/${created.environment_id}`)
    } catch (error) {
      toast({
        title: "Error",
        description: error instanceof Error ? error.message : "Failed to create environment.",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <MainLayout backLink={{ href: "/environments", label: "Back to Environments" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Add Environment</h1>

        <Card>
          <CardHeader>
            <CardTitle>Environment Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name *</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Prague Office"
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="e.g. Prague, Czech Republic"
                  disabled={isSubmitting}
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !name.trim()}>
                  {isSubmitting ? "Creating..." : "Create Environment"}
                </Button>
                <Button type="button" variant="outline" onClick={() => router.push("/environments")}>
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
