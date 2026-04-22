"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDriversApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";

export default function AddDriverPage() {
  const router = useRouter();
  const { toast } = useToast();
  const driversApi = useDriversApi();
  const [name, setName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    try {
      setIsSubmitting(true);
      const created = await driversApi.create({ name: name.trim() });

      toast({
        title: "Driver Created",
        description: `Driver ${created.name} (${created.driver_id}) has been created.`,
      });

      router.push(`/drivers/${created.driver_id}`);
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
    <MainLayout backLink={{ href: "/drivers", label: "Back to Drivers" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Add Driver</h1>

        <Card>
          <CardHeader>
            <CardTitle>Driver Information</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name *</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Enter driver name"
                  disabled={isSubmitting}
                  autoFocus
                />
              </div>

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !name.trim()}>
                  {isSubmitting ? "Creating..." : "Create Driver"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push("/drivers")}
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
