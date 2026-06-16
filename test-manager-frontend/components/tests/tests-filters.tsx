"use client";

import { useState, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";
import { useDebouncedCallback } from "use-debounce";

interface TestsFiltersProps {
  filters: {
    environment_id?: string;
    experiment_id?: string;
    q?: string;
  };
  onFilterChange: (key: string, value: string | undefined) => void;
  onClearFilters: () => void;
}

export function TestsFilters({
  filters,
  onFilterChange,
  onClearFilters,
}: TestsFiltersProps) {
  const [searchInput, setSearchInput] = useState(filters.q || "");

  useEffect(() => {
    setSearchInput(filters.q || "");
  }, [filters.q]);

  const hasActiveFilters =
    filters.environment_id || filters.experiment_id || filters.q;

  const debouncedFilterChange = useDebouncedCallback(
    (key: string, value: string | undefined) => {
      onFilterChange(key, value);
    },
    300,
  );

  return (
    <div className="flex gap-4 items-center">
      <Input
        placeholder="Search tests..."
        value={searchInput}
        onChange={(e) => {
          const value = e.target.value;
          setSearchInput(value);
          debouncedFilterChange("q", value || undefined);
        }}
        className="max-w-sm"
      />

      {hasActiveFilters && (
        <Button
          variant="outline"
          size="sm"
          onClick={onClearFilters}
          className="shrink-0"
        >
          <X className="mr-2 h-4 w-4" />
          Clear
        </Button>
      )}
    </div>
  );
}
