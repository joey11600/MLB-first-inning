"use client";

/**
 * OpsHealthCard — T2.54 ops health surface.
 *
 * Tiny status card that polls /api/health-live every 30s and surfaces:
 *   - Status pill (ok / warn / degraded / unknown)
 *   - Time since last predictor cycle (target: <10 min)
 *   - Time since last live-state worker tick (target: <30s during games)
 *   - Errors in last hour / last 24h
 *
 * Designed to live above SummaryStrip on the main board so the operator
 * sees system status at a glance.  Click expands to show the most
 * recent errors and per-step counts.
 */

import { useCallback, useEffect, useState } from "react";
import styles from "./OpsHealthCard.module.css";

type Status = "ok" | "warn" | "degraded" | "unknown";

interface ErrorRow {
  capturedAtUtc: string;
  step:          string;
  exitCode:      number | null;
  message:       string;
}

interface HealthResponse {
  status:               Status;
  reasons:              string[];
  checkedAt:            string;
  etHour:               number;
  isPrimeHours:         boolean;
  lastPredictAt:        string | null;
  lastWorkerAt:         string | null;
  // T4.13: grade-pipeline freshness.  lastGradeAt = MAX(graded_at) on
  // picks_<season>; gamesAwaitingGrade = games whose 1st inning landed
  // in live_game_state but graded_result is still empty in picks.  The
  // latter is the killer cron-lag signal -- 0 under healthy ops, >0
  // when grading has stalled.
  lastGradeAt?:         string | null;
  minutesSincePredict:  number | null;
  minutesSinceWorker:   number | null;
  minutesSinceGrade?:   number | null;
  gamesAwaitingGrade?:  number;
  oldestAwaitingMinutes?: number | null;
  errorsLast24h:        number;
  errorsLastHour:       number;
  errorCountsByStep:    Record<string, number>;
  recentErrors:         ErrorRow[];
  // T3.14: informational notices (e.g. calibration-drift) -- NOT errors.
  noticesLast24h?:      number;
  recentNotices?:       ErrorRow[];
  // 2026-08-23: The Odds API credit balance per spending host (railway =
  // the lock-time money path, gha = the daily multi-book snapshot).
  oddsCredits?:         { host: string; remaining: number; used: number | null; checkedAt: string }[];
  // 2026-08-23: where the latest predict run's weather came from, and any
  // frozen row the two writers disagree about.  Both exist because of the
  // CLE@COL incident -- a lost weather fetch scored a game on neutral
  // defaults minutes before its freeze, and the number then differed
  // between hosts for a game that had already finished.
  weather?: {
    date: string; live: number; cache: number; stale: number;
    onDefault: number; degraded: string[]; checkedAt: string;
  } | null;
  // `count` is the MATERIAL disagreements (>= 0.02); `minor` is the noise
  // floor two independent fetches always produce. Only count alerts.
  frozenDivergence?: { count: number; minor?: number; rows: Record<string, unknown>[]; checkedAt: string } | null;
}

/** "19,790" for one host, or "Railway 27 · GHA 19,790" when the two hosts
 *  plainly hold different keys (2026-08-23: they did).  The same key read
 *  at two moments differs by a handful of credits, so a small gap is shown
 *  as the most recent reading. */
function creditsText(rows: { host: string; remaining: number; checkedAt: string }[]): string {
  if (rows.length === 0) return "";
  const fmt = (n: number) => n.toLocaleString("en-US");
  const label = (h: string) => h === "railway" ? "Railway" : h === "gha" ? "GHA" : h;
  const sorted = [...rows].sort((a, b) => Date.parse(b.checkedAt) - Date.parse(a.checkedAt));
  if (sorted.length === 1) return fmt(sorted[0].remaining);
  const min = Math.min(...sorted.map(r => r.remaining));
  const max = Math.max(...sorted.map(r => r.remaining));
  if (max - min <= 300) return fmt(sorted[0].remaining);
  return sorted.map(r => `${label(r.host)} ${fmt(r.remaining)}`).join(" · ");
}

const POLL_MS = 30_000;

function formatAge(min: number | null): string {
  if (min === null) return "—";
  if (min < 1)   return "just now";
  if (min < 60)  return `${min} min ago`;
  if (min < 1440) return `${Math.floor(min / 60)} h ago`;
  return `${Math.floor(min / 1440)} d ago`;
}

function formatWorkerAge(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 30)   return "just now";
  if (sec < 60)   return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  return `${Math.floor(sec / 86400)} d ago`;
}

export function OpsHealthCard() {
  const [data, setData] = useState<HealthResponse | null>(null);
  // T3.14: track whether the user has manually toggled the card.  If the
  // status flips degraded/warn we auto-open the card so errors are
  // visible without clicking; once the user manually closes it the
  // auto-open is sticky-disabled until status returns to "ok" and goes
  // bad again.
  const [open, setOpen] = useState(false);
  const [userClosed, setUserClosed] = useState(false);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/health-live", { cache: "no-store" });
      if (!res.ok) return;
      const json: HealthResponse = await res.json();
      setData(json);
    } catch {
      /* swallow -- card just keeps showing the last successful state */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => { void refresh(); }, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // FIX 3 (2026-07-28) — AUTO-OPEN ONLY ON "degraded".
  //
  // This used to auto-open on ANY non-ok status, i.e. on "warn" too.  A
  // warn is routinely a single already-retried error sitting in the audit
  // log, and the expanded panel measured ~430px of which ~391px was not
  // errors — so the money numbers got shoved below the fold on a night
  // when nothing needed doing.  Now: "warn" shows the one-line summary row
  // (dot, status, ages, "N in the last hour · <step>") and the operator
  // clicks to see more; only "degraded" — the state that means tonight's
  // picks may actually be wrong — opens itself.
  // Skip if the user already closed it (sticky until status flips back
  // to ok and out again).
  useEffect(() => {
    if (!data) return;
    const isBad = data.status === "degraded";
    if (isBad && !open && !userClosed) {
      setOpen(true);
    }
    if (!isBad && userClosed) {
      // Status recovered — reset the userClosed flag so the next bad
      // event auto-opens again.
      setUserClosed(false);
    }
  }, [data, open, userClosed]);

  if (!data) {
    return (
      <section className={styles.wrap}>
        <div className={`${styles.card} ${styles.statusUnknown}`}>
          <span className={styles.dot} aria-hidden />
          <span className={styles.label}>System</span>
          <span className={styles.detail}>checking…</span>
        </div>
      </section>
    );
  }

  const { status, reasons, minutesSincePredict, lastWorkerAt,
          errorsLastHour, errorsLast24h, errorCountsByStep = {},
          recentErrors = [], noticesLast24h = 0, recentNotices = [],
          oddsCredits = [],
          weather = null,
          frozenDivergence = null,
          // T4.13: grade-freshness fields (optional -- older deploys
          // of the API route won't return them; default to safe values
          // so the card still renders against a stale endpoint).
          minutesSinceGrade = null,
          gamesAwaitingGrade = 0,
          oldestAwaitingMinutes = null } = data;

  const statusClass =
    status === "ok"       ? styles.statusOk
    : status === "warn"     ? styles.statusWarn
    : status === "degraded" ? styles.statusDegraded
    : styles.statusUnknown;

  const statusLabel =
    status === "ok"       ? "Healthy"
    : status === "warn"     ? "Warning"
    : status === "degraded" ? "Degraded"
    : "Unknown";

  const predictAge = formatAge(minutesSincePredict);
  const workerAge  = formatWorkerAge(lastWorkerAt);
  const gradeAge   = formatAge(minutesSinceGrade);

  // 2026-08-23: weather-provenance chip.  Only rendered when something is
  // NOT live -- on a healthy slate every game has a fresh reading and the
  // chip would be noise.  "default" is red (a real input loss); "stale" is
  // amber (the sticky fallback worked, the number did not move).
  const wxDegraded = weather ? weather.onDefault + weather.stale : 0;
  const wxCritical = (weather?.onDefault ?? 0) > 0;
  const wxTitle = !weather ? "" :
    `Weather inputs for the ${weather.date} slate:\n` +
    `${weather.live} live · ${weather.cache} cached · ${weather.stale} reused ` +
    `(last good reading) · ${weather.onDefault} on NEUTRAL DEFAULTS` +
    (weather.degraded.length ? `\nAffected: ${weather.degraded.join(", ")}` : "") +
    "\nA game on defaults was scored as a 20C calm day because the forecast " +
    "could not be fetched and nothing was cached.";

  // 2026-08-23: odds credits chip.  Amber under 2,000 (the snapshot's own
  // reserve line), red under 100 on Railway (the next slate locks unpriced).
  const creditsLabel = creditsText(oddsCredits);
  const railwayRem = oddsCredits.find(c => c.host === "railway")?.remaining;
  const creditsCritical = railwayRem !== undefined && railwayRem < 100;
  const creditsLow = !creditsCritical && oddsCredits.some(c => c.remaining < 2000);
  const creditsTitle = oddsCredits.length === 0 ? "" :
    "The Odds API credits remaining, as reported on the last run:\n" +
    oddsCredits.map(c =>
      `${c.host === "railway" ? "Railway (lock-time prices)" : c.host === "gha" ? "GitHub Actions (daily snapshot)" : c.host}: ` +
      `${c.remaining.toLocaleString("en-US")} left` +
      (c.used !== null ? ` (${c.used.toLocaleString("en-US")} used this cycle)` : "") +
      ` -- ${formatWorkerAge(c.checkedAt)}`,
    ).join("\n") +
    "\nBudget: ~75/day on the 20,000/month plan. Railway and GitHub must hold the SAME key.";

  // FIX 3 — the collapsed row has to be USEFUL on its own, or closing the
  // panel by default just hides the problem.  The newest failing step name
  // is the single most useful token ("grade-today", "scrape-dk"), so it
  // rides along on the errors chip and the operator can decide whether to
  // expand without expanding.
  const newestErrorStep = recentErrors.length > 0 ? recentErrors[0].step : null;

  // T4.13: tone the grade chip when the pipeline is lagging.  Red when
  // there's a real cron-lag bite (3+ ungraded for >15 min), amber when
  // 1-2 ungraded for >15 min.  Always-green when nothing's awaiting --
  // a cold lastGradeAt when no innings have completed yet (early in the
  // slate) is normal and shouldn't visually scream.
  const gradeLagging =
    gamesAwaitingGrade >= 3
    && oldestAwaitingMinutes !== null
    && oldestAwaitingMinutes > 15;
  const gradeWarn =
    !gradeLagging
    && gamesAwaitingGrade >= 1
    && oldestAwaitingMinutes !== null
    && oldestAwaitingMinutes > 15;
  const gradeChipClass = gradeLagging
    ? styles.metricErrs
    : gradeWarn
      ? styles.metricWarn ?? ""
      : "";

  return (
    <section className={styles.wrap}>
      <button
        type="button"
        className={`${styles.card} ${statusClass}`}
        onClick={() => {
          setOpen(o => {
            const next = !o;
            // Mark userClosed only if the system is actually degraded
            // (matches the auto-open trigger above). A warn never
            // auto-opens, so closing one has nothing to suppress.
            if (!next && status === "degraded") {
              setUserClosed(true);
            }
            return next;
          });
        }}
        aria-expanded={open}
        aria-label={`System status: ${statusLabel}. ${reasons.join(". ")}`}
        title={reasons.length > 0 ? reasons.join(" · ") : "All systems normal"}
      >
        <span className={styles.dot} aria-hidden />
        <span className={styles.label}>System</span>
        <span className={styles.statusText}>{statusLabel}</span>
        <span className={styles.sep}>·</span>
        <span className={styles.metric}>
          <span className={styles.metricLabel}>predict</span>
          <span className={`num ${styles.metricValue}`}>{predictAge}</span>
        </span>
        <span className={styles.sep}>·</span>
        <span className={styles.metric}>
          <span className={styles.metricLabel}>live state</span>
          <span className={`num ${styles.metricValue}`}>{workerAge}</span>
        </span>
        {/* T4.13: grade-freshness chip.  Always rendered so operators
            have a constant signal of grade-pipeline health (a missing
            chip would just look like the feature is broken).  Tone
            class kicks in only when the pipeline is actually lagging,
            so the chip stays visually quiet under healthy ops. */}
        <span className={styles.sep}>·</span>
        <span className={`${styles.metric} ${gradeChipClass}`}>
          <span className={styles.metricLabel}>grade</span>
          <span className={`num ${styles.metricValue}`}>{gradeAge}</span>
          {gamesAwaitingGrade > 0 && (
            <span
              className={styles.metricBadge}
              title={
                oldestAwaitingMinutes !== null
                  ? `${gamesAwaitingGrade} game${gamesAwaitingGrade === 1 ? "" : "s"}` +
                    ` awaiting grade -- oldest ${oldestAwaitingMinutes} min`
                  : `${gamesAwaitingGrade} game${gamesAwaitingGrade === 1 ? "" : "s"} awaiting grade`
              }
            >
              {gamesAwaitingGrade} awaiting
            </span>
          )}
        </span>
        {wxDegraded > 0 && (
          <>
            <span className={styles.sep}>·</span>
            <span
              className={`${styles.metric} ${wxCritical ? styles.metricErrs : styles.metricWarn}`}
              title={wxTitle}
            >
              <span className={styles.metricLabel}>weather</span>
              <span className={`num ${styles.metricValue}`}>
                {wxCritical ? `${weather?.onDefault} default` : `${weather?.stale} reused`}
              </span>
            </span>
          </>
        )}
        {(frozenDivergence?.count ?? 0) > 0 && (
          <>
            <span className={styles.sep}>·</span>
            <span
              className={`${styles.metric} ${styles.metricWarn}`}
              title={
                "Frozen rows where this host and the committed ledger hold " +
                "MATERIALLY different probabilities (>= 2 points). Report-only " +
                "(reconcile I6): a finished row is never rewritten. Converges " +
                "on the next Railway redeploy." +
                (frozenDivergence?.minor
                  ? `
${frozenDivergence.minor} more differ by under 2 points -- ` +
                    "the normal drift between two independent fetches."
                  : "")
              }
            >
              <span className={styles.metricLabel}>frozen split</span>
              <span className={`num ${styles.metricValue}`}>{frozenDivergence?.count}</span>
            </span>
          </>
        )}
        {creditsLabel && (
          <>
            <span className={styles.sep}>·</span>
            <span
              className={`${styles.metric} ${creditsCritical ? styles.metricErrs : creditsLow ? styles.metricWarn : ""}`}
              title={creditsTitle}
            >
              <span className={styles.metricLabel}>odds credits</span>
              <span className={`num ${styles.metricValue}`}>{creditsLabel}</span>
            </span>
          </>
        )}
        {errorsLast24h > 0 && (
          <>
            <span className={styles.sep}>·</span>
            <span className={`${styles.metric} ${styles.metricErrs}`}>
              <span className={styles.metricLabel}>errors 24h</span>
              <span className={`num ${styles.metricValue}`}>{errorsLast24h}</span>
              {errorsLastHour > 0 && (
                <span className={styles.metricBadge}>
                  {errorsLastHour} in the last hour
                  {newestErrorStep ? ` · ${newestErrorStep}` : ""}
                </span>
              )}
            </span>
          </>
        )}
        <span className={styles.expandHint} aria-hidden>
          {open ? "▴" : "▾"}
        </span>
        {loading && <span className={styles.refreshDot} aria-hidden />}
      </button>

      {open && (
        <div className={styles.expand}>
          {Object.keys(errorCountsByStep ?? {}).length > 0 && (
            <div className={styles.section}>
              <div className="eyebrow">Errors by step (last 24 h)</div>
              <ul className={styles.stepList}>
                {Object.entries(errorCountsByStep ?? {})
                  .sort((a, b) => b[1] - a[1])
                  .map(([step, count]) => (
                    <li key={step} className={styles.stepRow}>
                      <span className={styles.stepName}>{step}</span>
                      <span className={`num ${styles.stepCount}`}>{count}</span>
                    </li>
                  ))}
              </ul>
            </div>
          )}
          {recentErrors.length > 0 ? (
            <div className={styles.section}>
              <div className="eyebrow">Most recent errors</div>
              <ul className={styles.errList}>
                {recentErrors.map((e, i) => (
                  <li key={`${e.capturedAtUtc}-${i}`} className={styles.errRow}>
                    <span className={styles.errMeta}>
                      <span className={styles.errStep}>{e.step}</span>
                      <span className={styles.errTime}>
                        {formatAge(Math.floor((Date.now() - Date.parse(e.capturedAtUtc)) / 60000))}
                      </span>
                    </span>
                    <span className={styles.errMessage} title={e.message || "(no detail)"}>
                      {e.message || "(no detail)"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className={styles.section}>
              <span className={styles.allClear}>No errors in last 24 hours.</span>
            </div>
          )}

          {/* FIX 3 — notices are behind their own collapsed disclosure.
              They are not errors: the three live ones are `reconcile-heal`
              SUCCESSES, i.e. the system fixing itself.  Rendered open, in
              the same red-tinted row treatment as real errors, they were
              the single biggest contributor to the panel's height and they
              made a working repair look like a fault. */}
          {noticesLast24h > 0 && recentNotices.length > 0 && (
            <details className={styles.noticeBox}>
              <summary>
                {noticesLast24h} routine {noticesLast24h === 1 ? "notice" : "notices"} —
                {" "}things the system did on its own, not problems
              </summary>
              <ul className={styles.errList}>
                {recentNotices.map((e, i) => (
                  <li key={`notice-${e.capturedAtUtc}-${i}`} className={styles.noticeRow}>
                    <span className={styles.errMeta}>
                      <span className={styles.noticeStep}>{e.step}</span>
                      <span className={styles.errTime}>
                        {formatAge(Math.floor((Date.now() - Date.parse(e.capturedAtUtc)) / 60000))}
                      </span>
                    </span>
                    <span className={styles.errMessage} title={e.message || "(no detail)"}>
                      {e.message || "(no detail)"}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* The "Why {statusLabel}" list was deleted here.  It restated
              `reasons` verbatim, and `reasons` is already the summary
              button's title attribute AND its aria-label — so the same
              sentence rendered twice, once for sighted readers and once
              for screen readers, plus a third copy in the tooltip. */}
        </div>
      )}
    </section>
  );
}
