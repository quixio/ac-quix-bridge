"use client";

import { useState, useEffect, Children } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { buttonVariants, type ButtonProps } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { VariantProps } from "class-variance-authority";

interface NavigationButtonProps
  extends Omit<ButtonProps, "asChild" | "loading"> {
  href: string;
  children: React.ReactNode;
  prefetch?: boolean;
  variant?: VariantProps<typeof buttonVariants>["variant"];
  size?: VariantProps<typeof buttonVariants>["size"];
  className?: string;
}

/**
 * NavigationButton - A button that shows loading state during navigation
 *
 * Wraps Next.js Link styled as a Button that:
 * - Shows a spinner when clicked
 * - Disables itself during navigation
 * - Starts NProgress bar
 * - Automatically resets when navigation completes
 */
export function NavigationButton({
  href,
  children,
  prefetch = true,
  variant = "default",
  size = "default",
  className,
}: NavigationButtonProps) {
  const [isNavigating, setIsNavigating] = useState(false);
  const pathname = usePathname();

  // Reset loading state when navigation completes (pathname changes)
  useEffect(() => {
    setIsNavigating(false);
  }, [pathname]);

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    setIsNavigating(true);
  };

  // Extract text content by skipping the first child (icon)
  const childArray = Children.toArray(children);
  const textContent = childArray.length > 1 ? childArray.slice(1) : children;

  return (
    <Link
      href={href}
      prefetch={prefetch}
      onClick={handleClick}
      className={cn(
        buttonVariants({ variant, size }),
        isNavigating && "pointer-events-none opacity-50",
        "inline-flex items-center",
        className,
      )}
      {...(isNavigating && { "aria-disabled": true })}
    >
      {isNavigating ? (
        <>
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          {textContent}
        </>
      ) : (
        children
      )}
    </Link>
  );
}
