"use client";

import { useEffect, useMemo } from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface TestSummary {
  test_id: string;
  driver_name?: string | null;
}

export interface SessionSummary {
  session_id: string;
  track: string;
  car_model: string;
}

interface Props {
  tests: TestSummary[];
  sessionsByTest: Record<string, SessionSummary[]>;
  selectedTestId: string | null;
  selectedSessionId: string | null;
  onChange: (sel: { testId: string | null; sessionId: string | null }) => void;
  hideSessionPicker?: boolean;
}

export function TestSessionPicker({
  tests,
  sessionsByTest,
  selectedTestId,
  selectedSessionId,
  onChange,
  hideSessionPicker = false,
}: Props) {
  const sessions = useMemo(
    () => (selectedTestId ? sessionsByTest[selectedTestId] ?? [] : []),
    [selectedTestId, sessionsByTest],
  );

  const sortedSessions = useMemo(
    () =>
      [...sessions].sort((a, b) => b.session_id.localeCompare(a.session_id)),
    [sessions],
  );

  // Auto-pick latest session when test changes and nothing's selected yet.
  // Skipped in test-wide mode so we don't fight the URL/state with a sessionId.
  useEffect(() => {
    if (hideSessionPicker) return;
    if (selectedTestId && !selectedSessionId && sortedSessions.length > 0) {
      onChange({
        testId: selectedTestId,
        sessionId: sortedSessions[0].session_id,
      });
    }
  }, [
    hideSessionPicker,
    selectedTestId,
    selectedSessionId,
    sortedSessions,
    onChange,
  ]);

  return (
    <div
      className={
        hideSessionPicker
          ? "grid grid-cols-1 gap-4"
          : "grid grid-cols-1 md:grid-cols-2 gap-4"
      }
    >
      <div>
        <Label htmlFor="picker-test">Test</Label>
        <Select
          value={selectedTestId ?? ""}
          onValueChange={(v) =>
            onChange({ testId: v || null, sessionId: null })
          }
        >
          <SelectTrigger id="picker-test" className="w-full">
            <SelectValue placeholder="Pick a test..." />
          </SelectTrigger>
          <SelectContent>
            {tests.map((t) => (
              <SelectItem key={t.test_id} value={t.test_id}>
                {t.test_id} {t.driver_name ? `· ${t.driver_name}` : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {!hideSessionPicker && (
        <div>
          <Label htmlFor="picker-session">Session</Label>
          <Select
            value={selectedSessionId ?? ""}
            onValueChange={(v) =>
              onChange({ testId: selectedTestId, sessionId: v || null })
            }
            disabled={!selectedTestId || sortedSessions.length === 0}
          >
            <SelectTrigger id="picker-session" className="w-full">
              <SelectValue
                placeholder={
                  !selectedTestId
                    ? "Pick a test first"
                    : sortedSessions.length === 0
                      ? "No sessions yet"
                      : "Pick a session..."
                }
              />
            </SelectTrigger>
            <SelectContent>
              {sortedSessions.map((s) => (
                <SelectItem key={s.session_id} value={s.session_id}>
                  {s.session_id.slice(0, 16)} · {s.track} / {s.car_model}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedTestId && sortedSessions.length === 0 && (
            <p className="text-xs text-muted-foreground mt-1">
              No sessions on this test yet. Start an AC session first.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
