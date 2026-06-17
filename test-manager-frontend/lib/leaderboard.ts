// Leaderboard service UI deployment URL — baked at build time. `_ORIGIN` is the
// scheme+host+port, used to gate the auth-token postMessage handshake to the
// embedded iframe (same pattern as the Telemetry Explorer / Lakehouse embeds).
export const LEADERBOARD_UI_URL = process.env.NEXT_PUBLIC_LEADERBOARD_UI_URL ?? "";

export const LEADERBOARD_ORIGIN = (() => {
  try {
    return new URL(LEADERBOARD_UI_URL).origin;
  } catch {
    return "";
  }
})();
