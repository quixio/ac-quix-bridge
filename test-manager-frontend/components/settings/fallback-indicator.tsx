"use client";

import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

interface FallbackIndicatorProps {
  isFallback: boolean;
  message?: string;
  className?: string;
}

export function FallbackIndicator({
  isFallback,
  message = "Using auto-detected deployment from current workspace",
  className,
}: FallbackIndicatorProps) {
  if (!isFallback) {
    return null;
  }

  return (
    <div
      className={cn(
        "flex items-center gap-2 text-blue-700 bg-blue-50 dark:bg-blue-900/20 dark:text-blue-400 rounded-md px-3 py-2 text-sm",
        className,
      )}
    >
      <Info className="h-4 w-4 flex-shrink-0" />
      <span>{message}</span>
    </div>
  );
}
