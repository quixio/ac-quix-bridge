/**
 * JSON Diff Viewer Component
 * Displays side-by-side comparison of two JSON objects using Monaco DiffEditor
 */

"use client";

import { useMemo } from "react";
import dynamic from "next/dynamic";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

// Lazy load Monaco DiffEditor to reduce initial bundle size (~2-3MB)
const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.DiffEditor),
  {
    loading: () => <Skeleton className="h-[40vh] w-full" />,
    ssr: false,
  },
);

interface JsonDiffViewerProps {
  oldData: any;
  newData: any;
  title?: string;
}

export function JsonDiffViewer({
  oldData,
  newData,
  title = "Changes Preview",
}: JsonDiffViewerProps) {
  const originalJson = useMemo(
    () => JSON.stringify(oldData, null, 2),
    [oldData],
  );
  const modifiedJson = useMemo(
    () => JSON.stringify(newData, null, 2),
    [newData],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{title}</CardTitle>
        <p className="text-sm text-muted-foreground">Side-by-side comparison</p>
      </CardHeader>
      <CardContent>
        <div className="border rounded-md overflow-hidden">
          <DiffEditor
            original={originalJson}
            modified={modifiedJson}
            language="json"
            theme="vs-dark"
            height="40vh"
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 13,
              renderSideBySide: true,
              scrollBeyondLastLine: false,
              automaticLayout: true,
              scrollbar: {
                vertical: "auto",
                horizontal: "auto",
                verticalScrollbarSize: 10,
                horizontalScrollbarSize: 10,
                useShadows: false,
              },
            }}
          />
        </div>
      </CardContent>
    </Card>
  );
}
