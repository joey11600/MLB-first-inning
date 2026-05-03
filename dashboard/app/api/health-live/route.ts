/**
 * GET /api/health-live
 *
 * T2.54: Live ops health endpoint.  Queries Supabase directly (rather
 * than the dashboard's bundled CSV state, which is only refreshed on
 * git push) so the dashboard sees the TRUE state of the live Railway
 * predictor + worker, not the GHA cron state from the last push.
 *
 * Surfaces:
 *   - Latest `picks_<season>.updated_at` -> last predictor cycle wrote
 *   - Latest `live_game_state.updated_at` -> live-state worker heartbeat
 *   - `system_errors` rows in last 24h, grouped by step
 *
 * Status pill semantics:
 *   ok        - predict <10 min ago AND no errors in last hour
 *   warn      - predict 10-30 min ago, or errors in last hour but predict fresh
 *   degraded  - predict >30 min ago during prime hours, or many errors
 *   unknown   - Supabase unreachable / no data (probably config issue)
 *
 * Cache: no-store.  Polled by the OpsHealthCard component every ~30s.
 */

import { NextResponse } from "next/server";
import { getServerSupabase } from "@/lib/supabase";

export const runtime  = "nodejs";
export const dynamic  = "force-dynamic";

interface SystemError {
  capturedAtUtc: string;
  step:          string;
  exitCode:      number | null;
  message:       string;
}

export async function GET() {
  const sb = getServerSupabase();
  const now = new Date();
  const checkedAt = now.toISOString();

  if (!sb) {
    return NextResponse.json(
      {
        status: "unknown",
        reasons: ["Supabase not configured"],
        checkedAt,
      },
      { status: 200, headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  }

  const season = now.getUTCFullYear();

  // 1. Latest picks_<season>.updated_at (= last predictor cycle wrote)
  let lastPredictAt: string | null = null;
  try {
    const { data } = await sb
      .from(`picks_${season}`)
      .select("updated_at")
      .order("updated_at", { ascending: false })
      .limit(1);
    lastPredictAt = (data?.[0] as { updated_at?: string } | undefined)?.updated_at ?? null;
  } catch { /* swallow */ }

  // 2. Latest live_game_state.updated_at (= live-state worker heartbeat)
  let lastWorkerAt: string | null = null;
  try {
    const { data } = await sb
      .from("live_game_state")
      .select("updated_at")
      .order("updated_at", { ascending: false })
      .limit(1);
    lastWorkerAt = (data?.[0] as { updated_at?: string } | undefined)?.updated_at ?? null;
  } catch { /* swallow */ }

  // 3. system_errors in last 24h (grouped by step for the card UI)
  const cutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
  let recentErrors: SystemError[] = [];
  try {
    const { data } = await sb
      .from("system_errors")
      .select("captured_at_utc, step, exit_code, message")
      .gte("captured_at_utc", cutoff)
      .order("captured_at_utc", { ascending: false })
      .limit(100);
    recentErrors = (data ?? []).map((r: Record<string, unknown>) => ({
      capturedAtUtc: String(r.captured_at_utc ?? ""),
      step:          String(r.step ?? ""),
      exitCode:      typeof r.exit_code === "number" ? r.exit_code : null,
      message:       String(r.message ?? "").slice(0, 200),
    }));
  } catch { /* swallow */ }

  // Group error counts by step
  const errorCountsByStep: Record<string, number> = {};
  for (const e of recentErrors) {
    errorCountsByStep[e.step] = (errorCountsByStep[e.step] ?? 0) + 1;
  }

  // Errors in the last hour (more recent than the 24h window)
  const hourCutoff = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
  const errorsLastHour = recentErrors.filter(e => e.capturedAtUtc >= hourCutoff).length;

  // 4. Compute staleness
  const minutesSince = (iso: string | null): number | null => {
    if (!iso) return null;
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return null;
    return Math.floor((now.getTime() - t) / 60000);
  };
  const minSinceP = minutesSince(lastPredictAt);
  const minSinceW = minutesSince(lastWorkerAt);

  // 5. Determine prime-hours flag (ET)
  const etNow  = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const etHour = etNow.getHours();
  const isPrime = etHour >= 9 && etHour < 23;   // 9am - 11pm ET

  // 6. Status determination
  let status: "ok" | "warn" | "degraded" | "unknown" = "ok";
  const reasons: string[] = [];

  if (minSinceP === null) {
    status = "unknown";
    reasons.push("No predictor heartbeat in Supabase");
  } else if (isPrime && minSinceP > 30) {
    status = "degraded";
    reasons.push(`Predictor stale: last write ${minSinceP} min ago`);
  } else if (isPrime && minSinceP > 10) {
    status = "warn";
    reasons.push(`Predictor slow: last write ${minSinceP} min ago`);
  }

  if (errorsLastHour >= 6) {
    if (status === "ok" || status === "warn") status = "degraded";
    reasons.push(`${errorsLastHour} errors in the last hour`);
  } else if (errorsLastHour > 0) {
    if (status === "ok") status = "warn";
    reasons.push(`${errorsLastHour} error(s) in the last hour`);
  }

  return NextResponse.json(
    {
      status,
      reasons,
      checkedAt,
      etHour,
      isPrimeHours: isPrime,
      lastPredictAt,
      lastWorkerAt,
      minutesSincePredict: minSinceP,
      minutesSinceWorker:  minSinceW,
      errorsLast24h:       recentErrors.length,
      errorsLastHour,
      errorCountsByStep,
      // Cap response size; UI only shows the top-5 most recent
      recentErrors: recentErrors.slice(0, 5),
    },
    { status: 200, headers: { "Cache-Control": "no-store, max-age=0" } },
  );
}
