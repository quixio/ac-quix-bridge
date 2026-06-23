"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Home,
  FileText,
  Box,
  Server,
  ChevronLeft,
  Settings,
  Sliders,
  Users,
  TrendingUp,
  Database,
  FlaskConical,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { useSidebar } from "@/lib/contexts/sidebar-context";

interface NavItem {
  href: string;
  icon: React.ElementType;
  label: string;
}

const navItems: NavItem[] = [
  { href: "/", icon: Home, label: "Home" },
  { href: "/tests", icon: FileText, label: "Tests" },
  { href: "/devices", icon: Box, label: "Devices" },
  { href: "/environments", icon: Server, label: "Environments" },
  { href: "/experiments", icon: FlaskConical, label: "Experiments" },
  { href: "/drivers", icon: Users, label: "Drivers" },
];

const integrationItems: NavItem[] = [
  { href: "/config-manager", icon: Sliders, label: "Configurations" },
  { href: "/analysis", icon: TrendingUp, label: "Analysis" },
  { href: "/lakehouse", icon: Database, label: "Lakehouse" },
];

export function Sidebar() {
  const pathname = usePathname();
  const { collapsed, toggle, mobileOpen, setMobileOpen } = useSidebar();
  const closeMobile = () => setMobileOpen(false);
  // collapsed = desktop slim mode; meaningless for the full-width mobile drawer
  const isSlim = collapsed && !mobileOpen;

  return (
    <>
      {/* Backdrop (tablet/mobile drawer only) */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={closeMobile}
          aria-hidden="true"
        />
      )}
      <div
        className={cn(
          "fixed left-0 top-0 z-50 h-screen transition-all duration-300",
          "border-r bg-card flex flex-col w-64",
          isSlim ? "lg:w-16" : "lg:w-64",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          "lg:translate-x-0",
        )}
      >
      {/* Logo Section */}
      <div className="flex h-16 items-center border-b px-4">
        {!isSlim ? (
          <div className="flex items-baseline gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-primary">
              <span className="text-sm font-bold text-primary-foreground">
                TM
              </span>
            </div>
            <span className="text-lg font-semibold">Test Manager</span>
          </div>
        ) : (
          <div className="flex h-8 w-8 mx-auto shrink-0 items-center justify-center rounded bg-primary">
            <span className="text-sm font-bold text-primary-foreground">
              TM
            </span>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="space-y-2 p-3" aria-label="Main navigation">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center rounded-lg px-3 py-2.5 text-base font-medium transition-colors min-h-[44px]",
                "hover:bg-accent/50",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-accent-foreground",
                isSlim && "justify-center",
              )}
              title={isSlim ? item.label : undefined}
              aria-current={isActive ? "page" : undefined}
              onClick={closeMobile}
            >
              <Icon className="h-6 w-6 shrink-0" aria-hidden="true" />
              {!isSlim && <span className="ml-3">{item.label}</span>}
            </Link>
          );
        })}

        {/* Separator */}
        <div className="py-2">
          <div className="border-t border-border" />
        </div>

        {integrationItems.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center rounded-lg px-3 py-2.5 text-base font-medium transition-colors min-h-[44px]",
                "hover:bg-accent/50",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-accent-foreground",
                isSlim && "justify-center",
              )}
              title={isSlim ? item.label : undefined}
              aria-current={isActive ? "page" : undefined}
              onClick={closeMobile}
            >
              <Icon className="h-6 w-6 shrink-0" aria-hidden="true" />
              {!isSlim && <span className="ml-3">{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Bottom Section: Settings + Collapse */}
      <div
        className={cn(
          "mt-auto p-3 border-t",
          isSlim ? "space-y-2" : "flex items-center gap-2",
        )}
      >
        {/* Settings Link */}
        <Link
          href="/settings"
          className={cn(
            "flex items-center rounded-lg px-3 py-2.5 text-base font-medium transition-colors min-h-[44px]",
            "hover:bg-accent/50",
            pathname === "/settings"
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-accent-foreground",
            isSlim ? "justify-center" : "flex-1",
          )}
          title={isSlim ? "Settings" : undefined}
          aria-current={pathname === "/settings" ? "page" : undefined}
          onClick={closeMobile}
        >
          <Settings className="h-6 w-6 shrink-0" aria-hidden="true" />
          {!isSlim && <span className="ml-3">Settings</span>}
        </Link>

        {/* Collapse Button */}
        <button
          onClick={toggle}
          className={cn(
            "rounded-lg p-2.5 hover:bg-accent/50 min-w-[44px] min-h-[44px] hidden lg:flex items-center justify-center",
            "text-muted-foreground hover:text-accent-foreground transition-colors",
            collapsed ? "w-full mx-auto" : "w-auto",
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          <ChevronLeft
            className={cn(
              "h-6 w-6 transition-transform",
              collapsed && "rotate-180",
            )}
            aria-hidden="true"
          />
        </button>
      </div>
      </div>
    </>
  );
}
