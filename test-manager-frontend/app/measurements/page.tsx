"use client"

import { useEffect, useState, useRef } from "react"
import { useSearchParams } from "next/navigation"
import { MainLayout } from "@/components/layout/main-layout"
import { useIntegrationsApi } from "@/lib/hooks/use-api"
import { useQuixAuth } from "@/lib/contexts/quix-auth-context"
import { Loader2, BarChart3 } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

export default function MeasurementsPage() {
  const searchParams = useSearchParams()
  const testId = searchParams.get("test_id")
  const campaignId = searchParams.get("campaign_id")
  const environmentId = searchParams.get("environment_id")

  const integrationsApi = useIntegrationsApi()
  const { token } = useQuixAuth()

  const [iframeUrl, setIframeUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const iframeRef = useRef<HTMLIFrameElement>(null)

  // Fetch the Measurements URL
  useEffect(() => {
    const fetchUrl = async () => {
      try {
        setLoading(true)
        setError(null)

        const { url } = await integrationsApi.getMeasurementsUrl(
          testId,
          campaignId,
          environmentId
        )

        setIframeUrl(url)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load Measurements")
      } finally {
        setLoading(false)
      }
    }

    fetchUrl()
  }, [testId, campaignId, environmentId])

  // Set up postMessage listener for authentication
  useEffect(() => {
    if (!iframeUrl || !token) return

    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === "REQUEST_AUTH_TOKEN") {
        console.log("Measurements requested auth token")

        if (iframeRef.current?.contentWindow) {
          iframeRef.current.contentWindow.postMessage(
            {
              type: "AUTH_TOKEN",
              token: token,
            },
            "*" // In production, use specific origin for security
          )
          console.log("Auth token sent to Measurements")
        }
      }
    }

    window.addEventListener("message", handleMessage)

    return () => {
      window.removeEventListener("message", handleMessage)
    }
  }, [iframeUrl, token])

  if (loading) {
    return (
      <MainLayout>
        <div className="flex items-center justify-center min-h-[500px]">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-muted-foreground">Loading Measurements...</p>
          </div>
        </div>
      </MainLayout>
    )
  }

  if (error) {
    return (
      <MainLayout>
        <div className="flex items-center justify-center min-h-[500px]">
          <div className="max-w-md text-center space-y-4">
            <div className="mx-auto w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
              <BarChart3 className="h-8 w-8 text-primary" />
            </div>
            <h2 className="text-2xl font-semibold">Measurements</h2>
            <span className="text-sm text-muted-foreground">
              Coming soon
            </span>
          </div>
        </div>
      </MainLayout>
    )
  }

  if (!iframeUrl) {
    return (
      <MainLayout>
        <Alert>
          <AlertTitle>Measurements Unavailable</AlertTitle>
          <AlertDescription>
            Unable to load Measurements. Please try again later.
          </AlertDescription>
        </Alert>
      </MainLayout>
    )
  }

  return (
    <MainLayout noPadding>
      <iframe
        ref={iframeRef}
        src={iframeUrl}
        className="w-full h-[calc(100vh-4rem)] border-0"
        title="Measurements"
        allow="clipboard-read; clipboard-write"
      />
    </MainLayout>
  )
}
