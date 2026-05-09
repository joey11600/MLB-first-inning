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

  // Auto-open ONLY on non-ok status (warn/degraded) — historical errors in
  // the last 24h alone don't warrant pre-expanding. The status field already
  // encodes whether the system is currently unhappy; old resolved errors
  // sitting in the audit log shouldn't pop the panel on every visit.
  // Skip if the user already closed it (sticky until status flips back
  // to ok and out again).
  useEffect(() => {
    if (!data) return;
    const isBad = data.status !== "ok";
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
            // Mark userClosed only if the system is actually unhealthy
            // (matches the auto-open trigger). Historical errors alone
            // don't sticky-suppress the panel.
            if (!next && status !== "ok") {
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
        {errorsLast24h > 0 && (
          <>
            <span className={styles.sep}>·</span>
            <span className={`${styles.metric} ${styles.metricErrs}`}>
              <span className={styles.metricLabel}>errors 24h</span>
              <span className={`num ${styles.metricValue}`}>{errorsLast24h}</span>
              {errorsLastHour > 0 && (
                <span className={styles.metricBadge}>
                  {errorsLastHour} in last hour
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
                    <span className={styles.errMessage}>{e.message || "(no detail)"}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className={styles.section}>
              <span className={styles.allClear}>No errors in last 24 hours.</span>
            </div>
          )}
          {noticesLast24h > 0 && recentNotices.length > 0 && (
            <div className={styles.section}>
              <div className="eyebrow">Informational notices ({noticesLast24h})</div>
              <ul className={styles.errList}>
                {recentNotices.map((e, i) => (
                  <li key={`notice-${e.capturedAtUtc}-${i}`} className={styles.errRow}>
                    <span className={styles.errMeta}>
                      <span className={styles.errStep}>{e.step}</span>
                      <span className={styles.errTime}>
                        {formatAge(Math.floor((Date.now() - Date.parse(e.capturedAtUtc)) / 60000))}
                      </span>
                    </span>
                    <span className={styles.errMessage}>{e.message || "(no detail)"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {reasons.length > 0 && (
            <div className={styles.section}>
              <div className="eyebrow">Why {statusLabel}</div>
              <ul className={styles.reasonList}>
                {reasons.map((r, i) => (
                  <li key={i} className={styles.reasonRow}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
