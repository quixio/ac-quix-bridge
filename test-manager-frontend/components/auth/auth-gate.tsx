"use client";

import { Loader2 } from "lucide-react";
import { useQuixAuth } from "@/lib/contexts/quix-auth-context";

/**
 * Blocks page rendering until the auth context has finished initializing.
 * This must wrap children at the layout level so that page components
 * themselves don't mount (and don't fire their useEffect API calls) before
 * a token is available.
 *
 * Standalone mode with the auth dialog open is treated as "ready" so the
 * dialog itself can render and prompt the user.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { isLoading, token, showAuthDialog } = useQuixAuth();

  // Render children only when we have a usable token. Until then, show a
  // spinner. The auth dialog (rendered separately at the layout level) will
  // overlay the spinner in standalone mode without needing children to mount.
  if (!isLoading && token && !showAuthDialog) {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  );
}
