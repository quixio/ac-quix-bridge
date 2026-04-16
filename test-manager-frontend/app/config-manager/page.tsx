"use client";

import { useEffect, useState, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { MainLayout } from "@/components/layout/main-layout";
import { useIntegrationsApi } from "@/lib/hooks/use-api";
import { useQuixAuth } from "@/lib/contexts/quix-auth-context";
import { Loader2, Sliders } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export default function ConfigManagerPage() {
  const searchParams = useSearchParams();
  const configId = searchParams.get("config_id");
  const configVersion = searchParams.get("config_version");

  const integrationsApi = useIntegrationsApi();
  const { token } = useQuixAuth();

  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Check if running in local development mode
  const isLocalDev = process.env.NEXT_PUBLIC_LOCAL_DEV_MODE === "true";

  // Fetch the Config Manager frontend URL
  useEffect(() => {
    // Skip fetching in local dev mode
    if (isLocalDev) {
      setLoading(false);
      return;
    }

    const fetchUrl = async () => {
      try {
        setLoading(true);
        setError(null);

        const version = configVersion ? parseInt(configVersion, 10) : null;
        const { url } = await integrationsApi.getConfigManagerFrontendUrl(
          configId,
          version,
        );

        setIframeUrl(url);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load Configuration Manager",
        );
      } finally {
        setLoading(false);
      }
    };

    fetchUrl();
  }, [configId, configVersion, isLocalDev]);

  // Set up postMessage listener for authentication
  useEffect(() => {
    if (!iframeUrl || !token) return;

    const handleMessage = (event: MessageEvent) => {
      // Validate origin for security (in production, use specific origin)
      // For now, we accept messages from any origin since Config Manager URL is dynamic

      if (event.data?.type === "REQUEST_AUTH_TOKEN") {
        console.log("Config Manager requested auth token");

        // Send the auth token back to the iframe
        if (iframeRef.current?.contentWindow) {
          iframeRef.current.contentWindow.postMessage(
            {
              type: "AUTH_TOKEN",
              token: token,
            },
            "*", // In production, use specific origin for security
          );
          console.log("Auth token sent to Config Manager");
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
            <p className="text-muted-foreground">
              Loading Configuration Manager...
            </p>
          </div>
        </div>
      </MainLayout>
    );
  }

  // Show informative message in local development mode
  if (isLocalDev) {
    return (
      <MainLayout>
        <div className="flex items-center justify-center min-h-[500px]">
          <div className="max-w-md text-center space-y-6">
            <div className="mx-auto w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
              <Sliders className="h-8 w-8 text-primary" />
            </div>
            <div className="space-y-2">
              <h2 className="text-2xl font-semibold">Configuration Manager</h2>
              <p className="text-muted-foreground">
                The Dynamic Configuration Manager UI is only available when
                running in Quix Cloud.
              </p>
            </div>
            <p className="text-sm text-muted-foreground">
              In local development, a mock Configuration API is running at{" "}
              <code className="bg-muted px-1.5 py-0.5 rounded text-xs">
                localhost:8001
              </code>{" "}
              for backend integration testing.
            </p>
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
          <AlertTitle>Configuration Manager Unavailable</AlertTitle>
          <AlertDescription>
            Unable to load Configuration Manager. Please try again later.
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
        title="Configuration Manager"
        allow="clipboard-read; clipboard-write"
      />
    </MainLayout>
  );
}
