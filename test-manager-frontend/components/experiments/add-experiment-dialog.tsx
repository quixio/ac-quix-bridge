"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ExperimentCreateForm } from "./experiment-create-form";
import type { Experiment } from "@/types/experiment";

interface AddExperimentDialogProps {
  /** Called with the new experiment after creation (caller refetches + selects). */
  onCreated: (experiment: Experiment) => void;
}

/** "+" button beside an experiment picker that opens a dialog to create one
 * inline. The trigger is type="button" so it never submits a surrounding form.
 * The dialog content is DOM-portaled, but React still replays its <form> submit
 * up the component tree — so ExperimentCreateForm calls stopPropagation to keep
 * that submit out of an enclosing test <form>. */
export function AddExperimentDialog({ onCreated }: AddExperimentDialogProps) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Add experiment"
          className="shrink-0"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Experiment</DialogTitle>
          <DialogDescription>
            Create a new experiment and select it for this test.
          </DialogDescription>
        </DialogHeader>
        <ExperimentCreateForm
          onCreated={(experiment) => {
            setOpen(false);
            onCreated(experiment);
          }}
          onCancel={() => setOpen(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
