"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDriversApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import type { Driver } from "@/types/driver";

interface DriverCreateFormProps {
  /** Called with the created driver after a successful POST. */
  onCreated: (driver: Driver) => void;
  /** Renders a Cancel button when provided (e.g. inside a dialog). */
  onCancel?: () => void;
  submitLabel?: string;
  autoFocus?: boolean;
}

/** Driver create form (name/email/company). Shared by the /drivers/add page
 * and the inline add-driver dialog on the test forms. Caller decides what
 * happens after create (redirect, refetch + select, …) via onCreated. */
export function DriverCreateForm({
  onCreated,
  onCancel,
  submitLabel = "Create Driver",
  autoFocus = true,
}: DriverCreateFormProps) {
  const { toast } = useToast();
  const driversApi = useDriversApi();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const canSubmit = !!name.trim() && !!email.trim() && !!company.trim();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // Stop the submit bubbling through the Dialog portal (React replays events
    // up the component tree) into a surrounding test <form> and firing it.
    e.stopPropagation();
    if (!canSubmit) return;

    try {
      setIsSubmitting(true);
      const created = await driversApi.create({
        name: name.trim(),
        email: email.trim(),
        company: company.trim(),
      });

      toast({
        title: "Driver Created",
        description: `Driver ${created.name} (${created.driver_id}) has been created.`,
      });

      onCreated(created);
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error ? error.message : "Failed to create driver.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="name">Name *</Label>
        <Input
          id="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Enter driver name"
          disabled={isSubmitting}
          autoFocus={autoFocus}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="email">Email *</Label>
        <Input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="driver@example.com"
          disabled={isSubmitting}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="company">Company *</Label>
        <Input
          id="company"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          placeholder="Enter company"
          disabled={isSubmitting}
        />
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
