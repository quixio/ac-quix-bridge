"use client";

import { Button } from "@/components/ui/button";

interface Props {
  disabled: boolean;
  isAnalyzing: boolean;
  hasExistingAnalysis: boolean;
  onClick: () => void;
}

export function AnalyzeButton({
  disabled,
  isAnalyzing,
  hasExistingAnalysis,
  onClick,
}: Props) {
  return (
    <Button onClick={onClick} disabled={disabled || isAnalyzing}>
      {isAnalyzing
        ? "Analyzing..."
        : hasExistingAnalysis
          ? "Re-analyze"
          : "Analyze"}
    </Button>
  );
}
