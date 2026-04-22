"use client";

import { useEffect, useState, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { MainLayout } from "@/components/layout/main-layout";
import { useIntegrationsApi } from "@/lib/hooks/use-api";
import { useQuixAuth } from "@/lib/contexts/quix-auth-context";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export default function AnalyticsPage() {
  const searchParams = useSearchParams();
  const testId = searchParams.get("test_id");
  const campaignId = searchParams.get("campaign_id");
  const environmentId = searchParams.get("environment_id");

  const integrationsApi = useIntegrationsApi();
  const { token } = useQuixAuth();

  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Fetch the Analytics URL
  useEffect(() => {
    const fetchUrl = async () => {
      try {
        setLoading(true);
        setError(null);

        const { url } = await integrationsApi.getAnalyticsUrl(
          testId,
          campaignId,
          environmentId,
        );

        setIframeUrl(url);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load Analytics",
        );
      } finally {
        setLoading(false);
      }
    };

    fetchUrl();
  }, [testId, campaignId, environmentId]);

  // Set up postMessage listener for authentication
  useEffect(() => {
    if (!iframeUrl || !token) return;

    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === "REQUEST_AUTH_TOKEN") {
        console.log("Analytics requested auth token");

        if (iframeRef.current?.contentWindow) {
          iframeRef.current.contentWindow.postMessage(
            {
              type: "AUTH_TOKEN",
              token: token,
            },
            "*", // In production, use specific origin for security
          );
          console.log("Auth token sent to Analytics");
        }
      }
    };

    window.addEventListener("message", handleMessage);

    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [iframeUrl, token]);

  if (loading) {
    return (
      <MainLayout>
        <div className="flex items-center justify-center min-h-[500px]">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-muted-foreground">Loading Analytics...</p>
          </div>
        </div>
      </MainLayout>
    );
  }

  if (error) {
    return (
      <MainLayout>
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </MainLayout>
    );
  }

  if (!iframeUrl) {
    return (
      <MainLayout>
        <Alert>
          <AlertTitle>Analytics Unavailable</AlertTitle>
          <AlertDescription>
            Unable to load Analytics. Please try again later.
          </AlertDescription>
        </Alert>
      </MainLayout>
    );
  }

  return (
    <MainLayout noPadding>
      <iframe
        ref={iframeRef}
        src={iframeUrl}
        className="w-full h-[calc(100vh-4rem)] border-0"
        title="Analytics"
        allow="clipboard-read; clipboard-write"
      />
    </MainLayout>
  );
}
