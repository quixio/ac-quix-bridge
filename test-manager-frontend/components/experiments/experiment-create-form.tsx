"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useExperimentsApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import type { Experiment } from "@/types/experiment";

interface ExperimentCreateFormProps {
  /** Called with the created experiment after a successful POST. */
  onCreated: (experiment: Experiment) => void;
  /** Renders a Cancel button when provided (e.g. inside a dialog). */
  onCancel?: () => void;
  submitLabel?: string;
  autoFocus?: boolean;
}

/** Experiment create form (name only). Shared by the /experiments/add page and
 * the inline add-experiment dialog on the test forms. The name is the verbatim
 * lake partition identity, so it is immutable once created. */
export function ExperimentCreateForm({
  onCreated,
  onCancel,
  submitLabel = "Create Experiment",
  autoFocus = true,
}: ExperimentCreateFormProps) {
  const { toast } = useToast();
  const experimentsApi = useExperimentsApi();
  const [name, setName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const canSubmit = !!name.trim();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Stop the submit bubbling through the Dialog portal (React replays events
    // up the component tree) into a surrounding test <form> and firing it.
    e.stopPropagation();
    if (!canSubmit) return;

    try {
      setIsSubmitting(true);
      const created = await experimentsApi.create({ name: name.trim() });

      toast({
        title: "Experiment Created",
        description: `Experiment ${created.name} (${created.experiment_id}) has been created.`,
      });

      onCreated(created);
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to create experiment.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="experiment-name">Name *</Label>
        <Input
          id="experiment-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. tyre_pressure"
          disabled={isSubmitting}
          autoFocus={autoFocus}
        />
        <p className="text-xs text-muted-foreground">
          Becomes a permanent data label and can&apos;t be renamed. Avoid{" "}
          <code>/</code> and <code>\</code>.
        </p>
      </div>

      <div className="flex gap-3 pt-2">
        <Button type="submit" disabled={isSubmitting || !canSubmit}>
          {isSubmitting ? "Creating..." : submitLabel}
        </Button>
        {onCancel && (
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={isSubmitting}
          >
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
