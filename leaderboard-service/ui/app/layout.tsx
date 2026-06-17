import type { Metadata } from "next"
import "./globals.css"
import { QuixAuthProvider } from "@/lib/contexts/quix-auth-context"

export const metadata: Metadata = {
  title: "Leaderboard",
  description: "AC Quix Leaderboard",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark">
      <body>
        <QuixAuthProvider>{children}</QuixAuthProvider>
      </body>
    </html>
  )
}
