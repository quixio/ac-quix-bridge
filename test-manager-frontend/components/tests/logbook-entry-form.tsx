"use client";

import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate,
  SessionInfo,
} from "@/types/test";

const TEST_WIDE = "__test_wide__";

const logbookEntrySchema = z.object({
  content: z.string().min(1, "Content is required"),
  session_id: z.string(),
});

type LogbookEntryFormData = z.infer<typeof logbookEntrySchema>;

interface LogbookEntryFormProps {
  testId: string;
  entry?: LogbookEntry;
  sessions: SessionInfo[];
  defaultOperator?: string;
  onSubmit: (data: LogbookEntryCreate | LogbookEntryUpdate) => Promise<void>;
  onCancel: () => void;
  isSubmitting?: boolean;
}

export function LogbookEntryForm({
  entry,
  sessions,
  onSubmit,
  onCancel,
  isSubmitting = false,
}: LogbookEntryFormProps) {
  const sortedSessions = useMemo(
    () =>
      [...sessions].sort((a, b) => b.session_id.localeCompare(a.session_id)),
    [sessions],
  );

  const defaultSessionValue = useMemo(() => {
    if (entry?.session_id) return entry.session_id;
    if (sortedSessions.length > 0) return sortedSessions[0].session_id;
    return TEST_WIDE;
  }, [entry?.session_id, sortedSessions]);

  const {
    register,
    handleSubmit,
    setFocus,
    setValue,
    watch,
    formState: { errors },
  } = useForm<LogbookEntryFormData>({
    resolver: zodResolver(logbookEntrySchema),
    defaultValues: {
      content: entry?.content || "",
      session_id: defaultSessionValue,
    },
  });

  const sessionValue = watch("session_id");

  useEffect(() => {
    setFocus("content");
  }, [setFocus]);

  const handleFormSubmit = async (data: LogbookEntryFormData) => {
    await onSubmit({
      content: data.content,
      session_id: data.session_id === TEST_WIDE ? null : data.session_id,
    });
  };

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-5">
      <div className="space-y-2">
        <Label htmlFor="session_id">Session</Label>
        {sortedSessions.length === 0 ? (
          <>
            <input
              type="hidden"
              {...register("session_id")}
              value={TEST_WIDE}
            />
            <p className="text-sm text-muted-foreground">
              No sessions yet — entry will be test-wide. You can attach it to a
              session later.
            </p>
          </>
        ) : (
          <Select
            value={sessionValue}
            onValueChange={(value) =>
              setValue("session_id", value, { shouldValidate: true })
            }
          >
            <SelectTrigger id="session_id">
              <SelectValue placeholder="Select session" />
            </SelectTrigger>
            <SelectContent>
              {sortedSessions.map((s) => (
                <SelectItem key={s.session_id} value={s.session_id}>
                  {s.session_id} — {s.track} / {s.car_model}
                </SelectItem>
              ))}
              <SelectItem value={TEST_WIDE}>Test-wide (no session)</SelectItem>
            </SelectContent>
          </Select>
        )}
        {errors.session_id && (
          <p className="text-sm text-destructive mt-1.5">
            {errors.session_id.message}
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="content">Content *</Label>
        <Textarea
          id="content"
          {...register("content")}
          placeholder="Add a remark about the session..."
          rows={4}
        />
        {errors.content && (
          <p className="text-sm text-destructive mt-1.5">
            {errors.content.message}
          </p>
        )}
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving..." : entry ? "Update Entry" : "Add Entry"}
        </Button>
      </div>
    </form>
  );
}
