/**
 * GET /api/health-live
 *
 * T2.54: Live ops health endpoint.  Queries Supabase directly (rather
 * than the dashboard's bundled CSV state, which is only refreshed on
 * git push) so the dashboard sees the TRUE state of the live Railway
 * predictor + worker, not the GHA cron state from the last push.
 *
 * T4.13: extended with grade-freshness signals so the dashboard surfaces
 * GH-Actions cron-lag the moment it starts (was the root cause of the
 * 02:14 ET CWS@LAA STRONG YRFI staying ungraded for 58 min).  Three new
 * fields:
 *   - lastGradeAt        : MAX(graded_at) on picks_<season>
 *   - minutesSinceGrade  : age in minutes
 *   - gamesAwaitingGrade : count of date=today picks where graded_result
 *                          is empty AND the live worker has already
 *                          marked first_inning_complete=true
 *
 * The third metric is the killer signal: it filters out "no recent grade
 * because no games have finished their 1st inning yet" (legit) from
 * "games are landing but the grader is silent" (cron lag).  We only
 * escalate status to degraded on the latter.
 *
 * Surfaces:
 *   - Latest `picks_<season>.updated_at` -> last predictor cycle wrote
 *   - Latest `picks_<season>.graded_at`  -> last grade landed
 *   - Latest `live_game_state.updated_at` -> live-state worker heartbeat
 *   - Count of unmatched fi_complete vs ungraded picks
 *   - `system_errors` rows in last 24h, grouped by step
 *
 * Status pill semantics:
 *   ok        - predict <10 min ago AND no errors in last hour AND
 *               no completed innings awaiting grade for >15 min
 *   warn      - predict 10-30 min ago, OR errors in last hour but predict
 *               fresh, OR 1-2 games awaiting grade
 *   degraded  - predict >30 min ago during prime hours, OR many errors,
 *               OR 3+ games awaiting grade for >15 min (cron lag bite)
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

  // 2b. T4.13: Latest picks_<season>.graded_at = last grade landed.
  //
  // We sort .order("graded_at" desc) but with .not("graded_at","is",null)
  // because Postgres puts NULLs last by default in DESC order -- which
  // would land us a NULL graded_at for any row that hasn't graded yet,
  // not the actual most-recent grade.  An IS NOT NULL filter is enough
  // to get the real latest.
  let lastGradeAt: string | null = null;
  try {
    const { data } = await sb
      .from(`picks_${season}`)
      .select("graded_at")
      .not("graded_at", "is", null)
      .order("graded_at", { ascending: false })
      .limit(1);
    lastGradeAt = (data?.[0] as { graded_at?: string } | undefined)?.graded_at ?? null;
  } catch { /* swallow */ }

  // 2c. T4.13: Count of "1st inning complete but pick still ungraded"
  // for today's slate.  This is the killer cron-lag signal -- under
  // healthy operation it stays at 0 because the Railway worker grades
  // the moment fi_complete fires, and GH Actions catches anything the
  // worker missed within ~5 min.  When this number sits >0 for several
  // minutes during prime hours, something is broken in the grade
  // pipeline (worker down, Supabase RLS issue, GH Actions skipping).
  //
  // ET-aware "today" calculation matches Python's todays_iso() in
  // workers/live_state.py so dashboard + worker agree on which slate
  // to grade against.
  let gamesAwaitingGrade = 0;
  let oldestAwaitingMinutes: number | null = null;
  try {
    // Compute today's ET date the same way Python's
    // datetime.now(ET).strftime("%Y-%m-%d") would.  `en-CA` formats as
    // YYYY-MM-DD natively, so this is just the ET calendar day -- no
    // double-conversion through toISOString (which silently treats the
    // locale-string as local time and re-rolls to UTC).
    const etDateStr = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year:  "numeric",
      month: "2-digit",
      day:   "2-digit",
    }).format(now);

    // SELECT live games that have finished their 1st inning today.
    // Column is `fi_complete` in Supabase (the dashboard's TS interface
    // exposes it as `firstInningComplete` only after the API converts;
    // raw queries use the snake_case column name).
    const { data: liveData } = await sb
      .from("live_game_state")
      .select("game_pk, fi_complete, updated_at")
      .eq("date", etDateStr)
      .eq("fi_complete", true);

    const completePks = new Set<string>();
    const completedAt: Record<string, string> = {};
    for (const r of (liveData ?? []) as Array<{
      game_pk?: string | number;
      updated_at?: string;
    }>) {
      const gp = String(r.game_pk ?? "");
      if (gp) {
        completePks.add(gp);
        if (r.updated_at) completedAt[gp] = r.updated_at;
      }
    }

    if (completePks.size > 0) {
      // SELECT picks_<season> for the same date and check graded state.
      const { data: pickData } = await sb
        .from(`picks_${season}`)
        .select("game_pk, graded_result")
        .eq("date", etDateStr)
        .in("game_pk", Array.from(completePks));

      for (const r of (pickData ?? []) as Array<{
        game_pk?: string | number;
        graded_result?: string | null;
      }>) {
        const gp     = String(r.game_pk ?? "");
        const graded = (r.graded_result ?? "").toString().trim().toUpperCase();
        const isTerminal = graded === "WIN" || graded === "LOSS"
          || graded === "PASS" || graded === "POSTPONED"
          || graded === "SUSPENDED" || graded === "CANCELLED";
        if (!isTerminal) {
          gamesAwaitingGrade += 1;
          // Track the oldest fi-complete-but-ungraded game so the UI
          // can show "ungraded for X min" rather than just a count.
          const completedIso = completedAt[gp];
          if (completedIso) {
            const ageMin = Math.floor(
              (now.getTime() - Date.parse(completedIso)) / 60000,
            );
            if (Number.isFinite(ageMin)) {
              if (oldestAwaitingMinutes === null
                  || ageMin > oldestAwaitingMinutes) {
                oldestAwaitingMinutes = ageMin;
              }
            }
          }
        }
      }
    }
  } catch { /* swallow -- grade-freshness is advisory, must never break health */ }

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
    // NOTE: keep the FULL message here -- the known-noise classifier
    // below matches the terminal "Fetch failed: HTTP Error 403" line,
    // which sits past char 200 of the logged stderr tail.  Truncation
    // for display happens at response time instead.
    recentErrors = (data ?? []).map((r: Record<string, unknown>) => ({
      capturedAtUtc: String(r.captured_at_utc ?? ""),
      step:          String(r.step ?? ""),
      exitCode:      typeof r.exit_code === "number" ? r.exit_code : null,
      message:       String(r.message ?? ""),
    }));
  } catch { /* swallow */ }

  // T3.14: informational steps -- not actual failures.  Surfaced in the
  // expanded card under a separate "Notices" section instead of counting
  // against the error count or escalating health status.  Currently:
  //   - "calibration-drift": T2.59 monitor flags persistent prediction-vs-
  //     actual drift in a P(pick) bucket.  Often a positive signal (model
  //     is conservative) rather than a malfunction.
  //   - "reconcile-heal" (T-V21-2026-05-07g): tools/reconcile.py recorded
  //     auto-heals on this cycle.  POSITIVE signal -- means the safety
  //     net found drift and patched it.  Surface so the operator can
  //     see the system is repairing itself; don't count against errors.
  const INFO_STEPS = new Set<string>(["calibration-drift", "reconcile-heal"]);

  // 2026-08-05: the GHA (backup) DK scrape has been IP-blocked by DK's
  // CDN since ~2026-05-04 -- every hourly run logs a terminal
  // "Fetch failed: HTTP Error 403" row (~29/day) while the Railway
  // service does the actual capture.  Expected and routed-around, so
  // it is a NOTICE, not an error: counting it kept this badge pinned
  // at warn/degraded permanently.  Signature-matched (not step-matched)
  // so a scrape failure with any OTHER ending -- read timeout, the
  // 2026-08-05 zero-markets id rotation -- still counts as real.
  const isKnownBlockedScrape = (e: { step: string; message: string }) =>
    e.step === "scrape-dk-odds" &&
    e.message.includes("Fetch failed: HTTP Error 403");

  const isNotice = (e: { step: string; message: string }) =>
    INFO_STEPS.has(e.step) || isKnownBlockedScrape(e);
  const noticeRows  = recentErrors.filter(isNotice);
  const errorRows   = recentErrors.filter(e => !isNotice(e));

  // Group error counts by step (errors only, no notices)
  const errorCountsByStep: Record<string, number> = {};
  for (const e of errorRows) {
    errorCountsByStep[e.step] = (errorCountsByStep[e.step] ?? 0) + 1;
  }

  // Errors in the last hour (more recent than the 24h window)
  const hourCutoff = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
  const errorsLastHour = errorRows.filter(e => e.capturedAtUtc >= hourCutoff).length;

  // 4. Compute staleness
  const minutesSince = (iso: string | null): number | null => {
    if (!iso) return null;
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return null;
    return Math.floor((now.getTime() - t) / 60000);
  };
  const minSinceP = minutesSince(lastPredictAt);
  const minSinceW = minutesSince(lastWorkerAt);
  const minSinceG = minutesSince(lastGradeAt);

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

  // T4.13: Grade-pipeline status escalation.  Only counts games whose
  // 1st inning has actually completed -- so dawn-of-slate runs (no
  // games done yet, lastGradeAt = yesterday) don't false-positive.
  //
  // Threshold tuning: oldestAwaitingMinutes >15 means the live-state
  // worker has had at least 1.5 polls (10s cadence) to grade it AND
  // GH Actions has had a chance to fire -- if both missed it, that's
  // a real cron-lag bite, not a transient.  3+ ungraded escalates
  // straight to degraded because that's the pattern the operator hit
  // tonight (3 games stacked up while cron coalesced).
  if (gamesAwaitingGrade >= 3
      && oldestAwaitingMinutes !== null
      && oldestAwaitingMinutes > 15) {
    if (status === "ok" || status === "warn") status = "degraded";
    reasons.push(
      `${gamesAwaitingGrade} games ungraded ` +
      `(oldest ${oldestAwaitingMinutes}m) -- grade pipeline lagging`,
    );
  } else if (gamesAwaitingGrade >= 1
      && oldestAwaitingMinutes !== null
      && oldestAwaitingMinutes > 15) {
    if (status === "ok") status = "warn";
    reasons.push(
      `${gamesAwaitingGrade} game${gamesAwaitingGrade === 1 ? "" : "s"} ` +
      `ungraded for ${oldestAwaitingMinutes}m`,
    );
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
      lastGradeAt,
      minutesSincePredict: minSinceP,
      minutesSinceWorker:  minSinceW,
      minutesSinceGrade:   minSinceG,
      // T4.13: grade-pipeline observability.  UI uses these to render
      // the new "grade" chip + the "N awaiting" badge when nonzero.
      gamesAwaitingGrade,
      oldestAwaitingMinutes,
      errorsLast24h:       errorRows.length,
      errorsLastHour,
      errorCountsByStep,
      // Cap response size; UI only shows the top-5 most recent.
      // Message truncation happens HERE (not at parse time) so the
      // known-noise classifier above saw the full stderr tail.
      recentErrors:        errorRows.slice(0, 5)
                             .map(e => ({ ...e, message: e.message.slice(0, 200) })),
      // T3.14: informational notices, NOT errors.  e.g. calibration-drift,
      // or the known-blocked GHA scrape 403 wall (see above).
      noticesLast24h:      noticeRows.length,
      recentNotices:       noticeRows.slice(0, 5)
                             .map(e => ({ ...e, message: e.message.slice(0, 200) })),
    },
    { status: 200, headers: { "Cache-Control": "no-store, max-age=0" } },
  );
}
