"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useDriver } from "@/lib/hooks/use-drivers";
import { useDriversApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";

export default function EditDriverPage() {
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const driversApi = useDriversApi();
  const driverId = params.id as string;
  const { driver, loading } = useDriver(driverId);
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const canSubmit = !!email.trim() && !!company.trim();

  useEffect(() => {
    if (driver) {
      setEmail(driver.email ?? "");
      setCompany(driver.company ?? "");
    }
  }, [driver]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    try {
      setIsSubmitting(true);
      await driversApi.update(driverId, {
        email: email.trim(),
        company: company.trim(),
      });

      toast({
        title: "Driver Updated",
        description: `Driver ${driverId} has been updated.`,
      });

      router.push(`/drivers/${driverId}`);
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error ? error.message : "Failed to update driver.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (loading) {
    return (
      <MainLayout
        backLink={{ href: `/drivers/${driverId}`, label: "Back to Driver" }}
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
      backLink={{ href: `/drivers/${driverId}`, label: "Back to Driver" }}
    >
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Edit Driver</h1>

        <Card>
          <CardHeader>
            <CardTitle>Driver Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input id="name" value={driver?.name ?? ""} disabled />
                <p className="text-sm text-muted-foreground">
                  Permanent — can&apos;t be changed.
                </p>
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
                  autoFocus
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

              {!canSubmit && (
                <p className="text-sm text-muted-foreground">
                  Email and company are required to save.
                </p>
              )}

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !canSubmit}>
                  {isSubmitting ? "Saving..." : "Save Changes"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push(`/drivers/${driverId}`)}
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
