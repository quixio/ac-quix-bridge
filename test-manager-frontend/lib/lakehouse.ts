// Lakehouse Query UI deployment URL — baked at build time. `_ORIGIN` is the
// scheme+host+port, used to gate the auth-token postMessage handshake to the
// embedded iframe (same pattern as the Telemetry Explorer embed).
export const LAKEHOUSE_UI_URL =
  process.env.NEXT_PUBLIC_LAKEHOUSE_UI_URL ?? "";

export const LAKEHOUSE_ORIGIN = (() => {
  try {
    return new URL(LAKEHOUSE_UI_URL).origin;
  } catch {
    return "";
  }
})();

interface PartitionParams {
  environment?: string | null;
  test_rig?: string | null;
  experiment?: string | null;
  driver?: string | null;
  track?: string | null;
  carModel?: string | null;
}

/**
 * Build a partition-scoped peek query for the Lakehouse SQL editor.
 *
 * Session-level (sessionId given) pins the full Hive tuple; test-level pins
 * only the test-shared partition (environment/test_rig/experiment/driver) and
 * omits track/carModel/session_id (a test can span several). Always LIMIT 100
 * so it never pulls a full table.
 */
export function buildLakehouseQuery(
  p: PartitionParams,
  sessionId: string | null,
): string {
  const where: string[] = [];
  const eq = (col: string, v?: string | null) => {
    if (v) where.push(`${col} = '${v.replace(/'/g, "''")}'`);
  };
  eq("environment", p.environment);
  eq("test_rig", p.test_rig);
  eq("experiment", p.experiment);
  eq("driver", p.driver);
  if (sessionId) {
    eq("track", p.track);
    eq("carModel", p.carModel);
    where.push(`session_id = '${sessionId.replace(/'/g, "''")}'`);
  }
  const whereClause =
    where.length > 0 ? `\nWHERE ${where.join("\n  AND ")}` : "";
  return `SELECT *\nFROM ac_telemetry${whereClause}\nLIMIT 100`;
}

/** Build the Lakehouse UI iframe URL with the query prefilled (no auto-run —
 * the user reviews + runs it). */
export function lakehouseIframeUrl(sql: string): string {
  return `${LAKEHOUSE_UI_URL}?sql=${encodeURIComponent(sql)}`;
}
