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
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const canSubmit = !!name.trim() && !!email.trim() && !!company.trim();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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

              <div className="flex gap-3 pt-4">
                <Button type="submit" disabled={isSubmitting || !canSubmit}>
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
