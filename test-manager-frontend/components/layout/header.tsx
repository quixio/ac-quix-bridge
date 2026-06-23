"use client";

import Link from "next/link";
import { Search, User, X, ArrowLeft, LogOut, Menu } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useQuixAuth } from "@/lib/contexts/quix-auth-context";
import { useSidebar } from "@/lib/contexts/sidebar-context";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface BackLink {
  href: string;
  label: string;
}

interface HeaderProps {
  backLink?: BackLink;
}

export function Header({ backLink }: HeaderProps) {
  const [searchInput, setSearchInput] = useState("");
  const { userName, userEmail, isEmbedded, clearTokenAndPrompt } =
    useQuixAuth();
  const { toggleMobile, mobileOpen } = useSidebar();

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center border-b bg-background px-4 lg:px-6">
      <button
        onClick={toggleMobile}
        className="mr-2 flex h-10 w-10 items-center justify-center rounded-lg hover:bg-accent lg:hidden"
        aria-label={mobileOpen ? "Close navigation menu" : "Open navigation menu"}
        aria-expanded={mobileOpen}
      >
        <Menu className="h-6 w-6" />
      </button>
      <div className="flex flex-1 items-center justify-between">
        {/* Left side - Search or Back Navigation */}
        <div className="flex flex-1 items-center space-x-4">
          {backLink ? (
            <Link href={backLink.href}>
              <Button variant="ghost" size="sm">
                <ArrowLeft className="mr-2 h-4 w-4" />
                {backLink.label}
              </Button>
            </Link>
          ) : (
            <div className="relative max-w-md flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="h-9 w-full rounded-md border bg-transparent pl-10 pr-10 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-primary"
              />
              {searchInput && (
                <button
                  onClick={() => setSearchInput("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 hover:bg-muted"
                  aria-label="Clear search"
                >
                  <X className="h-4 w-4 text-muted-foreground" />
                </button>
              )}
            </div>
          )}
        </div>

        {/* Right side actions */}
        <div className="flex items-center space-x-4">
          {/* Environment indicator for local development */}
          {process.env.NODE_ENV === "development" && (
            <div className="rounded-full bg-yellow-500/10 px-3 py-1 text-xs font-medium text-yellow-600">
              Local Dev
            </div>
          )}

          {/* User menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex items-center space-x-2 rounded-lg p-2 hover:bg-accent">
                <div className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full bg-primary/10">
                  <User className="h-4 w-4" />
                </div>
                <div className="text-left">
                  <div className="text-sm font-medium">
                    {userName || "User"}
                  </div>
                </div>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>
                <div className="flex flex-col space-y-1">
                  <p className="text-sm font-medium leading-none">
                    {userName || "User"}
                  </p>
                  {userEmail && (
                    <p className="text-xs leading-none text-muted-foreground">
                      {userEmail}
                    </p>
                  )}
                </div>
              </DropdownMenuLabel>
              {!isEmbedded && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={clearTokenAndPrompt}
                    className="cursor-pointer"
                  >
                    <LogOut className="mr-2 h-4 w-4" />
                    <span>Close Session</span>
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
