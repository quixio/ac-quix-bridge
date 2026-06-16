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
import { DriverCreateForm } from "./driver-create-form";
import type { Driver } from "@/types/driver";

interface AddDriverDialogProps {
  /** Called with the new driver after creation (caller refetches + selects). */
  onCreated: (driver: Driver) => void;
}

/** "+" button beside a driver picker that opens a dialog to create a driver
 * inline. The dialog content is portaled, so its <form> does not nest inside
 * a surrounding form. The trigger is type="button" so it never submits one. */
export function AddDriverDialog({ onCreated }: AddDriverDialogProps) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Add driver"
          className="shrink-0"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Driver</DialogTitle>
          <DialogDescription>
            Create a new driver and select it for this test.
          </DialogDescription>
        </DialogHeader>
        <DriverCreateForm
          onCreated={(driver) => {
            setOpen(false);
            onCreated(driver);
          }}
          onCancel={() => setOpen(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
