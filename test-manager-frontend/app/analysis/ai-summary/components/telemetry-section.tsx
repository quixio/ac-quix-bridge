"use client";

import { useEffect, useState } from "react";
import { useAnalysesApi } from "@/lib/hooks/use-api";
import { SectionHeading } from "./analysis-card";

export function TelemetrySection({
  analysisId,
  status,
}: {
  analysisId: string;
  status: string;
}) {
  const analysesApi = useAnalysesApi();
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "complete") return;
    let cancelled = false;
    analysesApi
      .getTelemetry(analysisId)
      .then((r) => {
        if (!cancelled) setSvg(r.svg);
      })
      .catch(() => {
        if (!cancelled) setSvg(null);
      });
    return () => {
      cancelled = true;
    };
    // analysesApi reference is stable (memoised in useAnalysesApi)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId, status]);

  if (!svg) return null;

  const dataUri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  return (
    <section>
      <SectionHeading>Telemetry</SectionHeading>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={dataUri}
        alt="Lap telemetry"
        style={{ maxWidth: "100%", height: "auto" }}
      />
    </section>
  );
}
