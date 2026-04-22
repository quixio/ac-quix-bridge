import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { SidebarProvider } from "@/lib/contexts/sidebar-context";
import { ThemeContextProvider } from "@/lib/contexts/theme-context";
import { QuixAuthProvider } from "@/lib/contexts/quix-auth-context";
import { AuthTokenDialog } from "@/components/auth/auth-token-dialog";
import { AuthGate } from "@/components/auth/auth-gate";

export const metadata: Metadata = {
  title: "Test Manager",
  description: "Test Manager - Next.js Frontend",
};

// Force dynamic rendering for all pages (no static generation)
// This app requires client-side auth contexts that aren't available during static builds
export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <QuixAuthProvider>
          <AuthTokenDialog />
          <ThemeContextProvider>
            <SidebarProvider>
              <AuthGate>{children}</AuthGate>
              <Toaster />
            </SidebarProvider>
          </ThemeContextProvider>
        </QuixAuthProvider>
      </body>
    </html>
  );
}
