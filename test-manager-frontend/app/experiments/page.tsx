"use client";

import { Suspense, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SortingState } from "@tanstack/react-table";
import { MainLayout } from "@/components/layout/main-layout";
import { NavigationButton } from "@/components/ui/navigation-button";
import { ExperimentsTable } from "@/components/experiments/experiments-table";
import { EmptyState } from "@/components/shared/empty-state";
import { Pagination } from "@/components/shared/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { useExperiments } from "@/lib/hooks/use-experiments";
import { Plus, FlaskConical } from "lucide-react";

function ExperimentsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [filters, setFilters] = useState({
    q: searchParams.get("q") || undefined,
  });

  const [sorting, setSorting] = useState<SortingState>([
    { id: "experiment_id", desc: false },
  ]);

  const {
    experiments,
    loading,
    error,
    refetch,
    page,
    pageSize,
    total,
    totalPages,
    goToPage,
    changePageSize,
  } = useExperiments(filters);

  const updateFilters = useCallback(
    (newFilters: typeof filters) => {
      setFilters(newFilters);
      const params = new URLSearchParams();
      Object.entries(newFilters).forEach(([k, v]) => {
        if (v) params.set(k, v);
      });
      router.push(`/experiments?${params.toString()}`);
    },
    [router],
  );

  const handleSearch = useCallback(
    (value: string) => {
      updateFilters({ ...filters, q: value || undefined });
    },
    [filters, updateFilters],
  );

  const handleClearFilters = useCallback(() => {
    updateFilters({ q: undefined });
  }, [updateFilters]);

  return (
    <MainLayout>
      <div className="max-w-7xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Experiments</h1>
            <p className="text-muted-foreground">Manage experiments</p>
          </div>
          <NavigationButton href="/experiments/add">
            <Plus className="mr-2 h-4 w-4" />
            Add Experiment
          </NavigationButton>
        </div>

        <div className="space-y-4">
          <div className="flex gap-4">
            <Input
              placeholder="Search experiments..."
              defaultValue={filters.q}
              onChange={(e) => handleSearch(e.target.value)}
              className="max-w-sm"
            />
          </div>

          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : error ? (
            <EmptyState
              icon={<FlaskConical className="h-12 w-12" />}
              title="Failed to load experiments"
              description={error.message}
              action={{ label: "Retry", onClick: refetch }}
            />
          ) : experiments.length === 0 ? (
            <EmptyState
              icon={<FlaskConical className="h-12 w-12" />}
              title="No experiments found"
              description={
                filters.q
                  ? "No experiments match your filters. Try adjusting your criteria."
                  : "Get started by adding your first experiment."
              }
              action={
                filters.q
                  ? { label: "Clear Filters", onClick: handleClearFilters }
                  : {
                      label: "Add Experiment",
                      onClick: () => router.push("/experiments/add"),
                    }
              }
            />
          ) : (
            <>
              <ExperimentsTable
                data={experiments}
                sorting={sorting}
                onSortingChange={setSorting}
              />
              {total > 0 && (
                <Pagination
                  page={page}
                  pageSize={pageSize}
                  total={total}
                  totalPages={totalPages}
                  onPageChange={goToPage}
                  onPageSizeChange={changePageSize}
                />
              )}
            </>
          )}
        </div>
      </div>
    </MainLayout>
  );
}

export default function ExperimentsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-screen">
          <div>Loading...</div>
        </div>
      }
    >
      <ExperimentsPageContent />
    </Suspense>
  );
}
