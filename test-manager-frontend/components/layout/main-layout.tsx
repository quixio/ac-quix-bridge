"use client"

import { useState, useEffect } from "react"
import { Loader2 } from "lucide-react"
import { Sidebar } from "./sidebar"
import { Header } from "./header"
import { useSidebar } from "@/lib/contexts/sidebar-context"
import { useQuixAuth } from "@/lib/contexts/quix-auth-context"
import { cn } from "@/lib/utils/cn"

interface BackLink {
  href: string
  label: string
}

interface MainLayoutProps {
  children: React.ReactNode
  backLink?: BackLink
  noPadding?: boolean
}

export function MainLayout({ children, backLink, noPadding = false }: MainLayoutProps) {
  const { collapsed } = useSidebar()
  const { isLoading: authLoading, showAuthDialog } = useQuixAuth()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  // Block child rendering until auth context has finished initializing.
  // This prevents pages from firing API calls before the Portal postMessage
  // handshake completes (avoiding race-window 403s and empty-state flashes).
  // Standalone mode with the auth dialog open is treated as "ready" so the
  // dialog can render and prompt the user.
  const authReady = !authLoading || showAuthDialog

  return (
    <div className="relative flex min-h-screen">
      <Sidebar />
      <div
        className={cn(
          "flex-1 transition-all duration-300",
          mounted && collapsed ? "ml-16" : "ml-64"
        )}
      >
        <Header backLink={backLink} />
        <main className={noPadding ? "" : "p-6"}>
          {authReady ? (
            children
          ) : (
            <div className="flex items-center justify-center min-h-[60vh]">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
