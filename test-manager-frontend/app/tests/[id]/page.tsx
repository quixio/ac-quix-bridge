"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MainLayout } from "@/components/layout/main-layout";
import { Button } from "@/components/ui/button";
import { NavigationButton } from "@/components/ui/navigation-button";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { TestDetailCard } from "@/components/tests/test-detail-card";
import { LogbookEntryList } from "@/components/tests/logbook-entry-list";
import { LogbookEntryForm } from "@/components/tests/logbook-entry-form";
import { EmptyState } from "@/components/shared/empty-state";
import { useTestFull } from "@/lib/hooks/use-tests";
import { useTestsApi, useLogbookApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import {
  ArrowLeft,
  Edit,
  Trash2,
  FileText,
  Settings,
  Plus,
  Zap,
} from "lucide-react";
import type {
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate,
} from "@/types/test";

export default function TestDetailPage() {
  const params = useParams();
  const router = useRouter();
  const testId = params.id as string;
  const { toast } = useToast();
  const testsApi = useTestsApi();
  const logbookApi = useLogbookApi();

  const { testFull, loading, error, refetch } = useTestFull(testId);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isActivating, setIsActivating] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // Logbook state
  const [showLogbookDialog, setShowLogbookDialog] = useState(false);
  const [editingEntry, setEditingEntry] = useState<LogbookEntry | null>(null);
  const [isSubmittingLogbook, setIsSubmittingLogbook] = useState(false);

  if (loading) {
    return (
      <MainLayout backLink={{ href: "/tests", label: "Back to Tests" }}>
        <div className="max-w-7xl space-y-6">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </MainLayout>
    );
  }

  if (error || !testFull) {
    return (
      <MainLayout backLink={{ href: "/tests", label: "Back to Tests" }}>
        <div className="max-w-7xl">
          <EmptyState
            icon={<FileText className="h-12 w-12" />}
            title="Failed to load test"
            description={error?.message || "Test not found"}
            action={{
              label: "Retry",
              onClick: refetch,
            }}
          />
        </div>
      </MainLayout>
    );
  }

  // Destructure testFull for easier access
  const { test, logbook } = testFull;

  const handleActivate = async () => {
    setIsActivating(true);
    try {
      const updated = await testsApi.activate(testId);
      toast({
        title: "Test activated",
        description: `${testId} is now the active config (v${updated.config_version}).`,
      });
      refetch();
    } catch (error) {
      toast({
        title: "Error activating test",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setIsActivating(false);
    }
  };

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await testsApi.delete(testId);
      toast({
        title: "Test deleted",
        description: `Test ${testId} has been deleted successfully.`,
      });
      router.push("/tests");
    } catch (error) {
      toast({
        title: "Error deleting test",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
      setIsDeleting(false);
      setShowDeleteDialog(false);
    }
  };

  // Logbook handlers
  const handleCreateLogbookEntry = () => {
    setEditingEntry(null);
    setShowLogbookDialog(true);
  };

  const handleEditLogbookEntry = (entry: LogbookEntry) => {
    setEditingEntry(entry);
    setShowLogbookDialog(true);
  };

  const handleLogbookSubmit = async (
    data: LogbookEntryCreate | LogbookEntryUpdate,
  ) => {
    setIsSubmittingLogbook(true);
    try {
      if (editingEntry) {
        // Update existing entry
        await logbookApi.update(
          testId,
          editingEntry.id,
          data as LogbookEntryUpdate,
        );
        toast({
          title: "Entry updated",
          description: "Logbook entry has been updated successfully.",
        });
      } else {
        // Create new entry
        await logbookApi.create(testId, data as LogbookEntryCreate);
        toast({
          title: "Entry created",
          description: "Logbook entry has been created successfully.",
        });
      }

      // Refresh test data
      refetch();

      // Close dialog
      setShowLogbookDialog(false);
      setEditingEntry(null);
    } catch (error) {
      toast({
        title: editingEntry ? "Error updating entry" : "Error creating entry",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setIsSubmittingLogbook(false);
    }
  };

  const handleLogbookDeleted = () => {
    // Refresh test data
    refetch();
  };

  return (
    <MainLayout backLink={{ href: "/tests", label: "Back to Tests" }}>
      <div className="max-w-7xl space-y-6">
        {/* Header */}
        <div className="space-y-4">
          {/* Title and action buttons */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">
                {test.test_id}
              </h1>
              <p className="text-muted-foreground">Test execution details</p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="default"
                onClick={handleActivate}
                disabled={isActivating}
                data-testid="activate-test"
              >
                <Zap className="mr-2 h-4 w-4" />
                {isActivating ? "Activating..." : "Activate"}
              </Button>
              <NavigationButton
                variant="outline"
                href={`/tests/${testId}/edit`}
              >
                <Edit className="mr-2 h-4 w-4" />
                Edit Test
              </NavigationButton>
              <Button
                variant="destructive"
                onClick={() => setShowDeleteDialog(true)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete Test
              </Button>
            </div>
          </div>
        </div>

        {/* Delete Confirmation Dialog */}
        <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Are you sure?</AlertDialogTitle>
              <AlertDialogDescription>
                This will permanently delete test <strong>{testId}</strong>.
                This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={isDeleting}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                onClick={handleDelete}
                disabled={isDeleting}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              >
                {isDeleting ? "Deleting..." : "Delete"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* Content - single column */}
        <div className="max-w-4xl space-y-6">
          {/* Quick Access, Test Setup, Configuration, Timestamps */}
          <TestDetailCard
            test={test}
            onTestUpdated={refetch}
            resolvedNames={{
              pcName: test.pc_device_name || test.pc_device_id,
              rigName: test.test_rig_device_name || test.test_rig_device_id,
              envName: test.environment_name || test.environment_id,
            }}
          />

          {/* Logbook */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">
                  Logbook ({logbook.length})
                </CardTitle>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleCreateLogbookEntry}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  New Entry
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <LogbookEntryList
                testId={testId}
                entries={logbook}
                onEntryDeleted={handleLogbookDeleted}
                onEditEntry={handleEditLogbookEntry}
              />
            </CardContent>
          </Card>
        </div>

        {/* Logbook Entry Dialog */}
        <Dialog open={showLogbookDialog} onOpenChange={setShowLogbookDialog}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>
                {editingEntry ? "Edit Logbook Entry" : "New Logbook Entry"}
              </DialogTitle>
            </DialogHeader>
            <LogbookEntryForm
              testId={testId}
              entry={editingEntry || undefined}
              onSubmit={handleLogbookSubmit}
              onCancel={() => {
                setShowLogbookDialog(false);
                setEditingEntry(null);
              }}
              isSubmitting={isSubmittingLogbook}
            />
          </DialogContent>
        </Dialog>
      </div>
    </MainLayout>
  );
}
