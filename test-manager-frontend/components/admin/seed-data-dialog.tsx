"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAdminApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { Loader2 } from "lucide-react";

interface SeedDataDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}

export function SeedDataDialog({
  open,
  onOpenChange,
  onSuccess,
}: SeedDataDialogProps) {
  const adminApi = useAdminApi();
  const [numDevices, setNumDevices] = useState(10);
  const [numTests, setNumTests] = useState(10);
  const [includeJournals, setIncludeJournals] = useState(true);
  const [includeLogbook, setIncludeLogbook] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { toast } = useToast();

  const handleSubmit = async () => {
    // Validate ranges
    if (numDevices < 1 || numDevices > 100) {
      toast({
        title: "Invalid input",
        description: "Number of devices must be between 1 and 100",
        variant: "destructive",
      });
      return;
    }

    if (numTests < 1 || numTests > 100) {
      toast({
        title: "Invalid input",
        description: "Number of Tests must be between 1 and 100",
        variant: "destructive",
      });
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await adminApi.seedTestData({
        num_dacs: numDevices,
        num_tests: numTests,
        include_journals: includeJournals,
        include_logbook: includeLogbook,
      });

      toast({
        title: "Test data generated successfully",
        description: `Created ${result.dacs_created} devices, ${result.tests_created} Tests, ${result.journal_entries_created} journal entries, and ${result.logbook_entries_created} logbook entries`,
      });

      // Close dialog and notify parent
      onOpenChange(false);
      if (onSuccess) {
        onSuccess();
      }
    } catch (error) {
      toast({
        title: "Error generating test data",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Generate Test Data</DialogTitle>
          <DialogDescription>
            Configure how much test data to generate. This will create devices,
            tests, and related entries with realistic sample data.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          {/* Number of Devices */}
          <div className="grid grid-cols-4 items-center gap-4">
            <Label htmlFor="numDevices" className="text-right">
              Number of Devices
            </Label>
            <Input
              id="numDevices"
              type="number"
              min="1"
              max="100"
              value={numDevices}
              onChange={(e) => setNumDevices(parseInt(e.target.value) || 1)}
              className="col-span-3"
            />
          </div>

          {/* Number of Tests */}
          <div className="grid grid-cols-4 items-center gap-4">
            <Label htmlFor="numTests" className="text-right">
              Number of Tests
            </Label>
            <Input
              id="numTests"
              type="number"
              min="1"
              max="100"
              value={numTests}
              onChange={(e) => setNumTests(parseInt(e.target.value) || 1)}
              className="col-span-3"
            />
          </div>

          {/* Include Device Journals Checkbox */}
          <div className="flex items-center space-x-2 ml-auto">
            <Checkbox
              id="includeJournals"
              checked={includeJournals}
              onCheckedChange={(checked) =>
                setIncludeJournals(checked === true)
              }
            />
            <Label
              htmlFor="includeJournals"
              className="text-sm font-normal cursor-pointer"
            >
              Include device journal entries (3-4 per device)
            </Label>
          </div>

          {/* Include Test Logbook Checkbox */}
          <div className="flex items-center space-x-2 ml-auto">
            <Checkbox
              id="includeLogbook"
              checked={includeLogbook}
              onCheckedChange={(checked) => setIncludeLogbook(checked === true)}
            />
            <Label
              htmlFor="includeLogbook"
              className="text-sm font-normal cursor-pointer"
            >
              Include test logbook entries (2-5 per test)
            </Label>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isSubmitting}>
            {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isSubmitting ? "Generating..." : "Generate Test Data"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
