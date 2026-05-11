"use client";

/**
 * DemotionsBanner -- shows currently-active cluster demotions above
 * the board so the operator can't forget one is on.
 *
 * Why this exists: a demotion that was supposed to be a 4-day
 * experiment can quietly become permanent if nobody remembers to
 * look at the shadow data on the re-eval date.  This banner makes
 * the active state impossible to miss.
 *
 * Polls /api/active-demotions every 60s.  Renders nothing when no
 * active demotions exist.
 */

import { useEffect, useState } from "react";
import styles from "./DemotionsBanner.module.css";

interface DemotionInfo {
  id: string;
  reason: string;
  reevaluateAfter: string | null;
  daysUntilReeval: number | null;   // positive = future, 0 = today, negative = past due
  todayCount: number;
  last7Count: number;
}

interface ApiResp {
  demotions: DemotionInfo[];
}

const POLL_MS = 60_000;

function reevalLabel(d: DemotionInfo): { text: string; tone: "ok" | "soon" | "now" | "overdue" } {
  if (!d.reevaluateAfter || d.daysUntilReeval == null) {
    return { text: "no re-eval set", tone: "ok" };
  }
  const n = d.daysUntilReeval;
  if (n > 1) return { text: `re-eval in ${n} days`, tone: "ok" };
  if (n === 1) return { text: "re-eval tomorrow", tone: "soon" };
  if (n === 0) return { text: "re-eval TODAY", tone: "now" };
  return { text: `re-eval OVERDUE by ${Math.abs(n)}d`, tone: "overdue" };
}

export function DemotionsBanner() {
  const [data, setData] = useState<DemotionInfo[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/active-demotions", { cache: "no-store" });
        if (!res.ok) return;
        const json = (await res.json()) as ApiResp;
        if (!cancelled) setData(json.demotions || []);
      } catch {
        /* swallow */
      }
    };
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (!data || data.length === 0) {
    return null;
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.header}>
          <span className={styles.icon} aria-hidden="true">!</span>
          {data.length === 1 ? "Active cluster demotion" : `${data.length} active cluster demotions`}
        </div>
        {data.map((d) => {
          const re = reevalLabel(d);
          const toneClass =
            re.tone === "soon" ? styles.dueSoon
              : re.tone === "now" ? styles.dueNow
                : re.tone === "overdue" ? styles.overdue
                  : "";
          return (
            <div key={d.id}>
              <div className={styles.row}>
                <span className={styles.id}>{d.id}</span>
                <span className={`${styles.meta} ${toneClass}`}>
                  {re.text}
                </span>
                <span className={styles.count}>
                  today: <strong>{d.todayCount}</strong>
                </span>
                <span className={styles.count}>
                  trailing 7d: <strong>{d.last7Count}</strong>
                </span>
              </div>
              <div className={styles.reason}>{d.reason}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
