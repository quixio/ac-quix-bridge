"use client";

import type { ReactNode } from "react";

export function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
      <span className="h-4 w-1 rounded-full bg-primary" />
      {children}
    </h3>
  );
}
