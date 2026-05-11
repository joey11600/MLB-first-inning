"use client";

/**
 * ShadowPnlCard -- summarise active cluster demotions' shadow P&L
 * for the operator without requiring a CLI run.
 *
 * For each active cluster: shows REAL (placed-before-demotion) +
 * SHADOW (skipped-by-demotion) + TOTAL bets and net P&L.
 *
 * Decision tree (from docs/CLUSTER_DISCOVERY.md):
 *   SHADOW >= 5W-2L (clear winners skipped)  → demotion over-corrected
 *   SHADOW <= 2W-5L (clear losers skipped)   → demotion is right
 *   Mixed                                    → wait for more data
 */

import { useEffect, useState } from "react";
import styles from "./ShadowPnlCard.module.css";

interface BucketStats {
  n: number;
  wins: number;
  losses: number;
  pnl: number;
}

interface ClusterStats {
  id: string;
  real: BucketStats;
  shadow: BucketStats;
  total: BucketStats;
}

interface ApiResp {
  clusters: ClusterStats[];
}

const POLL_MS = 120_000;

function fmtPnl(p: number): string {
  if (Math.abs(p) < 0.01) return "0.00u";
  return `${p > 0 ? "+" : ""}${p.toFixed(2)}u`;
}

function Bucket({ label, b }: { label: string; b: BucketStats }) {
  const pnlClass =
    b.pnl > 0.01 ? styles.pnlPos
      : b.pnl < -0.01 ? styles.pnlNeg
        : "";
  return (
    <div className={styles.bucket}>
      <div className={styles.bucketLabel}>{label}</div>
      <div className={styles.bucketValue}>
        <span>
          {b.wins}W-{b.losses}L (n={b.n})
        </span>
        <span className={`${styles.pnl} ${pnlClass}`}>{fmtPnl(b.pnl)}</span>
      </div>
    </div>
  );
}

export function ShadowPnlCard() {
  const [data, setData] = useState<ClusterStats[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/shadow-pnl", { cache: "no-store" });
        if (!res.ok) return;
        const json = (await res.json()) as ApiResp;
        if (!cancelled) setData(json.clusters || []);
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

  if (!data) return null;
  if (data.length === 0) {
    return null;
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.title}>
          <span>Cluster demotion shadow P&amp;L</span>
        </div>
        {data.map((c) => (
          <div key={c.id} className={styles.cluster}>
            <div className={styles.id}>{c.id}</div>
            {c.real.n === 0 && c.shadow.n === 0 ? (
              <div className={styles.empty}>No graded matches yet.</div>
            ) : (
              <div className={styles.grid}>
                <Bucket label="Real (pre-demotion)" b={c.real} />
                <Bucket label="Shadow (skipped)" b={c.shadow} />
                <Bucket label="Total counterfactual" b={c.total} />
              </div>
            )}
          </div>
        ))}
        <div className={styles.foot}>
          Decision tree: SHADOW ≥ 5W-2L = over-corrected (turn off);
          SHADOW ≤ 2W-5L = real signal (keep on);
          mixed = wait for more data.
        </div>
      </div>
    </div>
  );
}
