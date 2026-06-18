"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useExperimentsApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";

export default function AddExperimentPage() {
  const router = useRouter();
  const { toast } = useToast();
  const experimentsApi = useExperimentsApi();
  const [name, setName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    try {
      setIsSubmitting(true);
      const created = await experimentsApi.create({
        name: name.trim(),
      });

      toast({
        title: "Experiment Created",
        description: `Experiment ${created.name} (${created.experiment_id}) has been created.`,
      });

      router.push(`/experiments/${created.experiment_id}`);
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
    <MainLayout
      backLink={{ href: "/experiments", label: "Back to Experiments" }}
    >
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Add Experiment</h1>

        <Card>
          <CardHeader>
            <CardTitle>Experiment Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name *</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Brake Cooling Study"
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !name.trim()}>
                  {isSubmitting ? "Creating..." : "Create Experiment"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push("/experiments")}
                >
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
