"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useEnvironment } from "@/lib/hooks/use-environments";
import { useEnvironmentsApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { EnvironmentStatus } from "@/types/environment";

export default function EditEnvironmentPage() {
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const environmentsApi = useEnvironmentsApi();
  const environmentId = params.id as string;
  const { environment, loading } = useEnvironment(environmentId);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [name, setName] = useState<string | null>(null);
  const [location, setLocation] = useState<string | null>(null);
  const [status, setStatus] = useState<EnvironmentStatus | null>(null);

  // Initialize form state from environment data (once)
  const formName = name ?? environment?.name ?? "";
  const formLocation = location ?? environment?.location ?? "";
  const formStatus = status ?? environment?.status ?? EnvironmentStatus.ACTIVE;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) return;

    try {
      setIsSubmitting(true);
      await environmentsApi.update(environmentId, {
        name: formName.trim(),
        location: formLocation.trim() || undefined,
        status: formStatus,
      });

      toast({
        title: "Environment Updated",
        description: `Environment ${environmentId} has been updated.`,
      });

      router.push(`/environments/${environmentId}`);
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to update environment.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (loading || !environment) {
    return (
      <MainLayout
        backLink={{
          href: `/environments/${environmentId}`,
          label: "Back to Environment",
        }}
      >
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    );
  }

  return (
    <MainLayout
      backLink={{
        href: `/environments/${environmentId}`,
        label: "Back to Environment",
      }}
    >
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Edit Environment</h1>

        <Card>
          <CardHeader>
            <CardTitle>Environment Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name *</Label>
                <Input
                  id="name"
                  value={formName}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Prague Office"
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  value={formLocation}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="e.g. Prague, Czech Republic"
                  disabled={isSubmitting}
                />
              </div>

              <div className="space-y-2">
                <Label>Status</Label>
                <Select
                  value={formStatus}
                  onValueChange={(v) => setStatus(v as EnvironmentStatus)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={EnvironmentStatus.ACTIVE}>
                      Active
                    </SelectItem>
                    <SelectItem value={EnvironmentStatus.INACTIVE}>
                      Inactive
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex gap-3 pt-4">
                <Button
                  type="submit"
                  disabled={isSubmitting || !formName.trim()}
                >
                  {isSubmitting ? "Saving..." : "Save Changes"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push(`/environments/${environmentId}`)}
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
