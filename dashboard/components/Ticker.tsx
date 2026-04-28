"use client";

import type { BoardRow } from "@/lib/types";
import styles from "./Ticker.module.css";

/**
 * Top-bar status strip.  Originally a scrolling marquee of every game's
 * matchup + lambda; that competed for attention with the main header.
 *
 * Now a static, condensed status bar: live indicator + zone count
 * chips (STRONG NRFI / PASS / STRONG YRFI) + slate average + game count.
 * Same height, a fraction of the cognitive weight.
 */
export function Ticker({ rows, date }: { rows: BoardRow[]; date: string }) {
  const avgLambda =
    rows.length === 0
      ? 0
      : rows.reduce((a, r) => a + r.lambda, 0) / rows.length;

  const strongNrfi = rows.filter(
    (r) => r.pickSide === "NRFI" && r.pickStrength === "STRONG",
  ).length;
  const strongYrfi = rows.filter(
    (r) => r.pickSide === "YRFI" && r.pickStrength === "STRONG",
  ).length;
  const pass = rows.length - strongNrfi - strongYrfi;

  return (
    <div className={styles.bar} aria-label="Slate ticker">
      <div className={styles.label}>
        <span className={styles.dot} />
        <span className={styles.labelText}>Live slate</span>
      </div>

      <div className={styles.summary}>
        <Chip label="Strong NRFI" count={strongNrfi} tone="nrfi" />
        <Chip label="Pass" count={pass} tone="pass" />
        <Chip label="Strong YRFI" count={strongYrfi} tone="yrfi" />
        <span className={styles.divider} aria-hidden />
        <span className={styles.metric}>
          <span className={styles.metricKey}>λ̄</span>
          <span className={styles.metricVal}>{avgLambda.toFixed(3)}</span>
        </span>
        <span className={styles.metric}>
          <span className={styles.metricKey}>games</span>
          <span className={styles.metricVal}>{rows.length}</span>
        </span>
      </div>

      <div className={styles.rightCap}>
        {date ? date.replace(/-/g, ".") : "—"}
      </div>
    </div>
  );
}

function Chip({
  label,
  count,
  tone,
}: {
  label: string;
  count: number;
  tone: "nrfi" | "yrfi" | "pass";
}) {
  return (
    <span className={styles.chip} data-tone={tone}>
      <span className={styles.chipCount}>{count}</span>
      <span className={styles.chipLabel}>{label}</span>
    </span>
  );
}
