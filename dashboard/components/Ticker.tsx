"use client";

import type { BoardRow } from "@/lib/types";
import styles from "./Ticker.module.css";

export function Ticker({ rows, date }: { rows: BoardRow[]; date: string }) {
  const avgLambda =
    rows.length === 0
      ? 0
      : rows.reduce((a, r) => a + r.lambda, 0) / rows.length;

  const segments = rows.map((r) => {
    const cls =
      r.pickSide === "NRFI"
        ? styles.nrfi
        : r.pickSide === "YRFI"
          ? styles.yrfi
          : styles.pass;
    return (
      <span key={`${r.rank}-${r.away}-${r.home}`} className={`${styles.seg} ${cls}`}>
        <span className={styles.matchup}>
          {r.away} @ {r.home}
        </span>
        <span className={styles.lambda}>λ {r.lambda.toFixed(3)}</span>
        <span className={styles.pct}>
          {r.pickSide === "YRFI"
            ? `Y ${r.yrfiPct.toFixed(1)}%`
            : `N ${r.nrfiPct.toFixed(1)}%`}
        </span>
      </span>
    );
  });

  return (
    <div className={styles.bar} role="marquee" aria-label="Slate ticker">
      <div className={styles.label}>
        <span className={styles.dot} />
        Live slate · <span className={styles.lambda}>λ̄ {avgLambda.toFixed(3)}</span> · {rows.length} games
      </div>
      <div className={styles.track}>
        <div className={styles.scroll}>
          {segments}
          {segments}
        </div>
      </div>
      <div className={styles.rightCap}>
        {date ? date.replace(/-/g, ".") : "—"}
      </div>
    </div>
  );
}
