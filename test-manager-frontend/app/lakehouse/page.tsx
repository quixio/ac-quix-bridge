"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent } from "@/components/ui/card";
import { useTestsApi } from "@/lib/hooks/use-api";
import { useQuixAuth } from "@/lib/contexts/quix-auth-context";
import {
  LAKEHOUSE_UI_URL,
  LAKEHOUSE_ORIGIN,
  buildLakehouseQuery,
  lakehouseIframeUrl,
} from "@/lib/lakehouse";

function LakehouseView() {
  const searchParams = useSearchParams();
  const testsApi = useTestsApi();
  const { token } = useQuixAuth();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);

  const testId = searchParams.get("test_id");
  const sessionId = searchParams.get("session_id");
  const track = searchParams.get("track");
  const carModel = searchParams.get("carModel");

  // Build the prefilled SQL. environment/test_rig/experiment/driver need the
  // backend's transformed partition values (build_partition_values); track/
  // carModel come from the clicked session (URL) when present.
  useEffect(() => {
    if (!LAKEHOUSE_UI_URL) return;
    let cancelled = false;
    const make = (p: {
      environment?: string | null;
      test_rig?: string | null;
      experiment?: string | null;
      driver?: string | null;
      track?: string | null;
      carModel?: string | null;
    }) =>
      lakehouseIframeUrl(
        buildLakehouseQuery(
          { ...p, track: track ?? p.track, carModel: carModel ?? p.carModel },
          sessionId,
        ),
      );

    const run = async () => {
      // Sidebar / no test context → open blank: browse the partition tree and
      // write SQL, no prefilled query.
      if (!testId) {
        if (!cancelled) setIframeUrl(LAKEHOUSE_UI_URL);
        return;
      }
      try {
        const params = await testsApi.getTelemetryParams(testId);
        if (!cancelled) setIframeUrl(make(params));
      } catch {
        // params load failed — open with whatever the URL carries (session deep-link).
        if (!cancelled) setIframeUrl(make({}));
      }
    };
    run();
    return () => {
      cancelled = true;
    };
    // testsApi is intentionally omitted (it's recreated each render); token is
    // the meaningful dep — re-fetch params if the auth token refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [testId, sessionId, track, carModel, token]);

  // Forward the auth token once on REQUEST_AUTH_TOKEN. Origin- AND source-
  // checked so the bearer only ever reaches the iframe we control.
  useEffect(() => {
    if (!iframeUrl || !token || !LAKEHOUSE_ORIGIN) return;
    const handler = (event: MessageEvent) => {
      if (event.origin !== LAKEHOUSE_ORIGIN) return;
      if (event.source !== iframeRef.current?.contentWindow) return;
      if (event.data?.type !== "REQUEST_AUTH_TOKEN") return;
      iframeRef.current?.contentWindow?.postMessage(
        { type: "AUTH_TOKEN", token },
        LAKEHOUSE_ORIGIN,
      );
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [iframeUrl, token]);

  const backLink = testId
    ? { href: `/tests/${testId}`, label: "Back to Test" }
    : { href: "/tests", label: "Back to Tests" };

  if (!LAKEHOUSE_UI_URL) {
    return (
      <MainLayout backLink={backLink}>
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Lakehouse is not configured. Set the{" "}
            <code>NEXT_PUBLIC_LAKEHOUSE_UI_URL</code> environment variable.
          </CardContent>
        </Card>
      </MainLayout>
    );
  }

  return (
    <MainLayout backLink={backLink} noPadding>
      {!iframeUrl ? (
        <div className="flex h-[70vh] items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <iframe
          ref={iframeRef}
          src={iframeUrl}
          className="h-[calc(100vh-8rem)] w-full rounded-md border"
          title="Lakehouse"
        />
      )}
    </MainLayout>
  );
}

export default function LakehousePage() {
  return (
    <Suspense
      fallback={
        <MainLayout>
          <div className="flex h-[70vh] items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        </MainLayout>
      }
    >
      <LakehouseView />
    </Suspense>
  );
}
