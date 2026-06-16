"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { DeviceStatusBadge } from "@/components/devices/device-status-badge";
import { useDevice } from "@/lib/hooks/use-devices";
import { useDevicesApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import { DeviceCategoryLabels } from "@/types/device";
import { Pencil, Trash2, Box } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

export default function DeviceDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const devicesApi = useDevicesApi();
  const deviceId = params.id as string;
  const { device, loading, error } = useDevice(deviceId);
  const { formatDate } = useDateFormatter();
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    try {
      setIsDeleting(true);
      await devicesApi.delete(deviceId);
      toast({
        title: "Device Deleted",
        description: `Device ${deviceId} has been deleted.`,
      });
      router.push("/devices");
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error ? error.message : "Failed to delete device.",
        variant: "destructive",
      });
    } finally {
      setIsDeleting(false);
    }
  };

  if (loading) {
    return (
      <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    );
  }

  if (error || !device) {
    return (
      <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
        <EmptyState
          icon={<Box className="h-12 w-12" />}
          title="Device not found"
          description={
            error?.message || "The requested device could not be found."
          }
          action={{
            label: "Back to Devices",
            onClick: () => router.push("/devices"),
          }}
        />
      </MainLayout>
    );
  }

  return (
    <MainLayout backLink={{ href: "/devices", label: "Back to Devices" }}>
      <div className="max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{device.name}</h1>
            <Badge variant="outline">
              {DeviceCategoryLabels[device.category]}
            </Badge>
            <DeviceStatusBadge status={device.status} />
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push(`/devices/${deviceId}/edit`)}
            >
              <Pencil className="mr-2 h-4 w-4" />
              Edit
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm" disabled={isDeleting}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete Device</AlertDialogTitle>
                  <AlertDialogDescription>
                    Are you sure you want to delete {device.name} ({deviceId})?
                    This action cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={handleDelete}>
                    Delete
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Device Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Device ID</p>
                <p className="font-medium">{device.device_id}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Name</p>
                <p className="font-medium">{device.name}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Category</p>
                <Badge variant="outline">
                  {DeviceCategoryLabels[device.category]}
                </Badge>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Status</p>
                <DeviceStatusBadge status={device.status} />
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Created</p>
                <p className="font-medium">{formatDate(device.created_at)}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Updated</p>
                <p className="font-medium">{formatDate(device.updated_at)}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
