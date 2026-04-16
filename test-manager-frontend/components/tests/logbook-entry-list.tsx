"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
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
import { useToast } from "@/lib/hooks/use-toast";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import { useLogbookApi } from "@/lib/hooks/use-api";
import type { LogbookEntry } from "@/types/test";
import { Clock, User, Pencil, Trash2, Activity } from "lucide-react";

interface LogbookEntryListProps {
  testId: string;
  entries: LogbookEntry[];
  onEntryDeleted: () => void;
  onEditEntry: (entry: LogbookEntry) => void;
}

export function LogbookEntryList({
  testId,
  entries,
  onEntryDeleted,
  onEditEntry,
}: LogbookEntryListProps) {
  const logbookApi = useLogbookApi();
  const [deletingEntryId, setDeletingEntryId] = useState<string | null>(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const { toast } = useToast();
  const { formatDateTime } = useDateFormatter();

  const handleDeleteClick = (entryId: string) => {
    setDeletingEntryId(entryId);
    setShowDeleteDialog(true);
  };

  const handleDeleteConfirm = async () => {
    if (!deletingEntryId) return;

    try {
      await logbookApi.delete(testId, deletingEntryId);
      toast({
        title: "Entry deleted",
        description: "The logbook entry has been deleted successfully.",
      });
      onEntryDeleted();
    } catch (error) {
      toast({
        title: "Error deleting entry",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setShowDeleteDialog(false);
      setDeletingEntryId(null);
    }
  };

  if (entries.length === 0) {
    return (
      <Card className="p-8 text-center">
        <Activity className="h-12 w-12 text-muted-foreground mx-auto mb-3" />
        <p className="text-sm text-muted-foreground">No logbook entries yet</p>
        <p className="text-xs text-muted-foreground mt-1">
          Create your first entry to track test progress
        </p>
      </Card>
    );
  }

  // Sort entries by timestamp (newest first)
  const sortedEntries = [...entries].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  const entryToDelete = entries.find((e) => e.id === deletingEntryId);

  return (
    <>
      <div className="relative space-y-4">
        {/* Timeline line */}
        <div className="absolute left-[11px] top-6 bottom-6 w-px bg-border" />

        {sortedEntries.map((entry, index) => (
          <div key={entry.id} className="relative flex gap-4">
            {/* Timeline dot */}
            <div className="relative flex-shrink-0">
              <div className="h-6 w-6 rounded-full border-2 border-primary bg-background flex items-center justify-center">
                <div className="h-2 w-2 rounded-full bg-primary" />
              </div>
            </div>

            {/* Entry card */}
            <Card className="flex-1 p-4">
              <div className="space-y-3">
                {/* Header */}
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-1 flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Clock className="h-3.5 w-3.5" />
                      <span>{formatDateTime(entry.created_at)}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onEditEntry(entry)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleDeleteClick(entry.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                {/* Content */}
                <div className="text-sm whitespace-pre-wrap">
                  {entry.content}
                </div>
              </div>
            </Card>
          </div>
        ))}
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete logbook entry?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this logbook entry?
              {entryToDelete && (
                <div className="mt-2 p-2 bg-muted rounded text-sm">
                  {entryToDelete.content.substring(0, 100)}
                  {entryToDelete.content.length > 100 && "..."}
                </div>
              )}
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
