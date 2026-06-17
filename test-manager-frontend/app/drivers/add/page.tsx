"use client";

import { useRouter } from "next/navigation";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DriverCreateForm } from "@/components/drivers/driver-create-form";

export default function AddDriverPage() {
  const router = useRouter();

  return (
    <MainLayout backLink={{ href: "/drivers", label: "Back to Drivers" }}>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold">Add Driver</h1>

        <Card>
          <CardHeader>
            <CardTitle>Driver Information</CardTitle>
          </CardHeader>
          <CardContent>
            <DriverCreateForm
              onCreated={(driver) =>
                router.push(`/drivers/${driver.driver_id}`)
              }
              onCancel={() => router.push("/drivers")}
            />
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
