"use client"

import { useState, useEffect } from "react"
import { Sidebar } from "./sidebar"
import { Header } from "./header"
import { useSidebar } from "@/lib/contexts/sidebar-context"
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
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

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
          {children}
        </main>
      </div>
    </div>
  )
}
