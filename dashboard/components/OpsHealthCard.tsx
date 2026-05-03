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
  minutesSincePredict:  number | null;
  minutesSinceWorker:   number | null;
  errorsLast24h:        number;
  errorsLastHour:       number;
  errorCountsByStep:    Record<string, number>;
  recentErrors:         ErrorRow[];
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
  const [open, setOpen] = useState(false);
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
          errorsLastHour, errorsLast24h, errorCountsByStep,
          recentErrors } = data;

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

  return (
    <section className={styles.wrap}>
      <button
        type="button"
        className={`${styles.card} ${statusClass}`}
        onClick={() => setOpen(o => !o)}
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
          {Object.keys(errorCountsByStep).length > 0 && (
            <div className={styles.section}>
              <div className="eyebrow">Errors by step (last 24 h)</div>
              <ul className={styles.stepList}>
                {Object.entries(errorCountsByStep)
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
