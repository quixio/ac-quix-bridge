"use client";

import { Button } from "@/components/ui/button";

interface Props {
  disabled: boolean;
  isAnalyzing: boolean;
  hasExistingAnalysis: boolean;
  mode?: "session" | "test-wide";
  onClick: () => void;
}

export function AnalyzeButton({
  disabled,
  isAnalyzing,
  hasExistingAnalysis,
  mode = "session",
  onClick,
}: Props) {
  const target = mode === "test-wide" ? " test" : "";
  return (
    <Button onClick={onClick} disabled={disabled || isAnalyzing}>
      {isAnalyzing
        ? "Analyzing..."
        : hasExistingAnalysis
          ? `Re-analyze${target}`
          : `Analyze${target}`}
    </Button>
  );
}
