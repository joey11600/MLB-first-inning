"use client";

/**
 * ShadowDeltaCard -- T4.4 surface for the daily T4.2 shadow report.
 *
 * Polls /api/shadow-summary every ~5 min and shows the 7-day delta_pl
 * between V2 actual placed bets and V2 + T4.2 priors-pooling shadow.
 *
 * Status pill semantics (computed server-side):
 *   ok       7d delta > 0u             -- T4.2 producing positive value
 *   warn     7d delta in [-1u, 0u]     -- mild dip, monitor
 *   regress  7d delta < -1u  OR  5+ consecutive negative days
 *   unknown  no rows yet (first night's grade hasn't fired)
 *
 * Click to expand and see the trailing 14 days as a small table.
 *
 * See PLAYBOOK section 3 for what to do when this turns red.
 */

import { useCallback, useEffect, useState } from "react";
import styles from "./ShadowDeltaCard.module.css";

type Status = "ok" | "warn" | "regress" | "unknown";

interface SummaryRow {
  date:     string;
  nBets:    number;
  v2W:      number;
  v2L:      number;
  v2Pl:     number;
  t42W:     number;
  t42L:     number;
  t42Pass:  number;
  t42Pl:    number;
  deltaPl:  number;
}

interface Aggregate {
  nBets:   number;
  v2Pl:    number;
  t42Pl:   number;
  deltaPl: number;
  nDays:   number;
}

interface ShadowSummaryResponse {
  status:                  Status;
  reason:                  string;
  rows:                    SummaryRow[];
  last7d:                  Aggregate;
  last14d:                 Aggregate;
  consecutiveNegativeDays: number;
}

const POLL_MS = 5 * 60_000;

function fmtUnit(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}u`;
}

export function ShadowDeltaCard() {
  const [data,    setData]    = useState<ShadowSummaryResponse | null>(null);
  const [open,    setOpen]    = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/shadow-summary", { cache: "no-store" });
      if (!res.ok) return;
      const json: ShadowSummaryResponse = await res.json();
      setData(json);
    } catch {
      /* swallow */
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
          <span className={styles.label}>T4.2 Shadow</span>
          <span className={styles.statusText}>checking…</span>
        </div>
      </section>
    );
  }

  const { status, reason, rows, last7d, last14d, consecutiveNegativeDays } = data;
  const statusClass =
      status === "ok"      ? styles.statusOk
    : status === "warn"    ? styles.statusWarn
    : status === "regress" ? styles.statusRegress
    : styles.statusUnknown;
  const statusLabel =
      status === "ok"      ? "Working"
    : status === "warn"    ? "Mild dip"
    : status === "regress" ? "Regression"
    : "No data";

  const hasData = rows.length > 0;

  return (
    <section className={styles.wrap}>
      <button
        type="button"
        className={`${styles.card} ${statusClass}`}
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        title={reason}
      >
        <span className={styles.dot} aria-hidden />
        <span className={styles.label}>T4.2 Shadow</span>
        <span className={styles.statusText}>{statusLabel}</span>

        {hasData && (
          <>
            <span className={styles.sep}>·</span>
            <span className={styles.metric}>
              <span className={styles.metricLabel}>7d delta</span>
              <span className={styles.metricValue}>{fmtUnit(last7d.deltaPl)}</span>
            </span>
            <span className={styles.sep}>·</span>
            <span className={styles.metric}>
              <span className={styles.metricLabel}>14d delta</span>
              <span className={styles.metricValue}>{fmtUnit(last14d.deltaPl)}</span>
            </span>
            {consecutiveNegativeDays >= 3 && (
              <>
                <span className={styles.sep}>·</span>
                <span className={`${styles.metric} ${styles.warn}`}>
                  <span className={styles.metricLabel}>NEG STREAK</span>
                  <span className={styles.metricValue}>{consecutiveNegativeDays}d</span>
                </span>
              </>
            )}
          </>
        )}

        <span className={styles.chevron} aria-hidden>{open ? "▲" : "▼"}</span>
      </button>

      {open && hasData && (
        <div className={styles.expanded}>
          <p className={styles.reason}>{reason}</p>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Date</th>
                <th className={styles.th}>n bets</th>
                <th className={styles.th}>V2 actual</th>
                <th className={styles.th}>V2+T4.2 shadow</th>
                <th className={styles.th}>delta</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.date} className={styles.tr}>
                  <td className={styles.td}>{r.date}</td>
                  <td className={styles.tdNum}>{r.nBets}</td>
                  <td className={styles.tdNum}>
                    {r.v2W}-{r.v2L} {fmtUnit(r.v2Pl)}
                  </td>
                  <td className={styles.tdNum}>
                    {r.t42W}-{r.t42L} ({r.t42Pass} P) {fmtUnit(r.t42Pl)}
                  </td>
                  <td className={`${styles.tdNum} ${r.deltaPl > 0 ? styles.deltaPos : r.deltaPl < 0 ? styles.deltaNeg : ""}`}>
                    {fmtUnit(r.deltaPl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className={styles.note}>
            T4.2 shadow simulates V2 + priors-pooled xera/whiff on every placed
            bet. Positive delta means T4.2 would have outperformed V2 actual.
            See <code>docs/PLAYBOOK.md</code> section 3 for what to do when
            this turns red.
          </p>
        </div>
      )}
    </section>
  );
}
