"use client"

import { Button } from "@/components/ui/button"
import { Rocket, X, RefreshCw, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { DeploymentReference } from "@/lib/types/portal"

export type DeploymentVariant = "config" | "measurements" | "analytics"

interface DeploymentDisplayProps {
  deployment: DeploymentReference | null
  variant: DeploymentVariant
  isFallback?: boolean
  isRefreshing?: boolean
  isClearing?: boolean
  onClear: () => void
  onChange: () => void
  onRefresh?: () => void
  className?: string
}

/**
 * Get the display URLs based on variant
 * - config: Shows both API URL and UI URL
 * - measurements: Shows both API URL ({url}/api/query) and UI URL
 * - analytics: Shows only UI URL
 */
function getDisplayUrls(deployment: DeploymentReference | null, variant: DeploymentVariant) {
  if (!deployment) return { apiUrl: null, uiUrl: null }

  switch (variant) {
    case "config":
      return {
        apiUrl: deployment.internal_url,
        uiUrl: deployment.embedded_view_url || deployment.public_url,
      }
    case "measurements": {
      const measUiUrl = deployment.public_url || deployment.embedded_view_url
      return {
        apiUrl: measUiUrl ? `${measUiUrl}/api/query` : null,
        uiUrl: measUiUrl,
      }
    }
    case "analytics":
      return {
        apiUrl: null, // No API calls
        uiUrl: deployment.embedded_view_url || deployment.public_url,
      }
  }
}

export function DeploymentDisplay({
  deployment,
  variant,
  isFallback = false,
  isRefreshing = false,
  isClearing = false,
  onClear,
  onChange,
  onRefresh,
  className,
}: DeploymentDisplayProps) {
  if (!deployment) {
    return null
  }

  const { apiUrl, uiUrl } = getDisplayUrls(deployment, variant)

  return (
    <div className={cn("flex items-start justify-between p-3 border rounded-md bg-muted/30", className)}>
      <div className="flex items-start gap-3 flex-1 min-w-0">
        <Rocket className={cn("h-5 w-5 mt-0.5 flex-shrink-0", isFallback ? "text-blue-500" : "text-green-500")} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium truncate">{deployment.deployment_name}</span>
            {isFallback && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                Auto-detected
              </span>
            )}
          </div>

          {/* URL display based on variant */}
          <div className="space-y-0.5 mt-1">
            {(variant === "config" || variant === "measurements") && (
              <div className="text-sm text-muted-foreground truncate">
                <span className="text-xs font-medium mr-1">API:</span>
                {apiUrl || <span className="italic text-muted-foreground/60">not found</span>}
              </div>
            )}
            {(variant === "config" || variant === "measurements") ? (
              <div className="text-sm text-muted-foreground truncate">
                <span className="text-xs font-medium mr-1">UI:</span>
                {uiUrl || <span className="italic text-muted-foreground/60">not found</span>}
              </div>
            ) : uiUrl ? (
              <div className="text-sm text-muted-foreground truncate">
                {uiUrl}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="flex gap-1 flex-shrink-0 ml-2">
        {onRefresh && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            disabled={isRefreshing}
            title="Refresh deployment info"
          >
            {isRefreshing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={onChange}
        >
          Change
        </Button>
        {!isFallback && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onClear}
            disabled={isClearing}
            title="Clear selection"
          >
            {isClearing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <X className="h-4 w-4" />
            )}
          </Button>
        )}
      </div>
    </div>
  )
}
