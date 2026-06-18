"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { MainLayout } from "@/components/layout/main-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { useExperiment } from "@/lib/hooks/use-experiments";
import { useExperimentsApi } from "@/lib/hooks/use-api";
import { useToast } from "@/lib/hooks/use-toast";
import { useDateFormatter } from "@/lib/hooks/use-date-formatter";
import { Trash2, FlaskConical } from "lucide-react";
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

export default function ExperimentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const experimentsApi = useExperimentsApi();
  const experimentId = params.id as string;
  const { experiment, loading, error } = useExperiment(experimentId);
  const { formatDate } = useDateFormatter();
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    try {
      setIsDeleting(true);
      await experimentsApi.delete(experimentId);
      toast({
        title: "Experiment Deleted",
        description: `Experiment ${experimentId} has been deleted.`,
      });
      router.push("/experiments");
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to delete experiment.",
        variant: "destructive",
      });
    } finally {
      setIsDeleting(false);
    }
  };

  if (loading) {
    return (
      <MainLayout
        backLink={{ href: "/experiments", label: "Back to Experiments" }}
      >
        <div className="max-w-2xl space-y-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-48 w-full" />
        </div>
      </MainLayout>
    );
  }

  if (error || !experiment) {
    return (
      <MainLayout
        backLink={{ href: "/experiments", label: "Back to Experiments" }}
      >
        <EmptyState
          icon={<FlaskConical className="h-12 w-12" />}
          title="Experiment not found"
          description={
            error?.message || "The requested experiment could not be found."
          }
          action={{
            label: "Back to Experiments",
            onClick: () => router.push("/experiments"),
          }}
        />
      </MainLayout>
    );
  }

  return (
    <MainLayout
      backLink={{ href: "/experiments", label: "Back to Experiments" }}
    >
      <div className="max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{experiment.name}</h1>
          </div>
          <div className="flex gap-2">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm" disabled={isDeleting}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete Experiment</AlertDialogTitle>
                  <AlertDialogDescription>
                    Are you sure you want to delete {experiment.name} (
                    {experimentId})? This action cannot be undone.
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
            <CardTitle>Experiment Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Experiment ID</p>
                <p className="font-medium">{experiment.experiment_id}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Name</p>
                <p className="font-medium">{experiment.name}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Created</p>
                <p className="font-medium">
                  {formatDate(experiment.created_at)}
                </p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Updated</p>
                <p className="font-medium">
                  {formatDate(experiment.updated_at)}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
