"use client";

import type { BoardRow } from "@/lib/types";
import styles from "./SummaryStrip.module.css";

/* ============================================================
   T3.21: SummaryStrip slimmed.

   Previously had 4 tiles: Games today, Avg λ, Today P&L, Today CLV
   (conditional), plus the distribution row.  The Today P&L and
   Today CLV tiles moved into RoiPanel's new TODAY window option so
   the operator no longer has to mentally segregate "today" from
   "30d window" stats -- they're all in one consolidated card.

   What's left here is the slate-shape summary: how many games are
   on tonight's board, what the average expected first-inning runs
   look like, and how the picks distribute across the three zones
   (Strong NRFI / Pass / Strong YRFI).  Pure descriptive stats; no
   P&L.
   ============================================================ */

interface Bucket {
  key: string;
  label: string;
  className: string;
  test: (r: BoardRow) => boolean;
}

const BUCKETS: Bucket[] = [
  {
    key: "strong-nrfi",
    label: "Strong NRFI",
    className: "strongNrfi",
    test: (r) => r.pickSide === "NRFI" && r.pickStrength === "STRONG",
  },
  {
    key: "pass",
    label: "Pass",
    className: "pass",
    test: (r) =>
      r.pickSide === "PASS" ||
      (r.pickStrength !== "STRONG" && r.pickStrength !== "LEAN"),
  },
  {
    key: "strong-yrfi",
    label: "Strong YRFI",
    className: "strongYrfi",
    test: (r) => r.pickSide === "YRFI" && r.pickStrength === "STRONG",
  },
];

export function SummaryStrip({
  rows,
}: {
  rows: BoardRow[];
}) {
  const avgLambda =
    rows.length === 0 ? 0 : rows.reduce((a, r) => a + r.lambda, 0) / rows.length;
  const maxLambda =
    rows.length === 0 ? 0 : Math.max(...rows.map((r) => r.lambda));
  const minLambda =
    rows.length === 0 ? 0 : Math.min(...rows.map((r) => r.lambda));
  const nrfiCnt = rows.filter((r) => r.pickSide === "NRFI").length;
  const yrfiCnt = rows.filter((r) => r.pickSide === "YRFI").length;

  return (
    <section className={styles.wrap} aria-label="Slate summary">
      <div className={styles.tiles}>
        <div className={`${styles.tile} ${styles.primary}`}>
          <div className="eyebrow">Games today</div>
          <div className={styles.big}>{rows.length}</div>
          <div className={styles.foot}>
            <span className={styles.footCell}>
              <span className={styles.tinyDot} data-tone="nrfi" />NRFI {nrfiCnt}
            </span>
            <span className={styles.footCell}>
              <span className={styles.tinyDot} data-tone="yrfi" />YRFI {yrfiCnt}
            </span>
          </div>
        </div>

        <div className={styles.tile}>
          <div className="eyebrow">Avg λ</div>
          <div className={styles.big}>{avgLambda.toFixed(3)}</div>
          <div className={styles.foot}>
            <span className="num">{minLambda.toFixed(3)}</span>
            <span className={styles.sep}>—</span>
            <span className="num">{maxLambda.toFixed(3)}</span>
          </div>
        </div>

        <div className={styles.distribution}>
          <div className="eyebrow">Distribution</div>
          <div className={styles.buckets}>
            {BUCKETS.map((b) => {
              const count = rows.filter(b.test).length;
              const pct = rows.length === 0 ? 0 : (count / rows.length) * 100;
              return (
                <div
                  key={b.key}
                  className={`${styles.bucket} ${styles[b.className]}`}
                >
                  <div className={styles.bucketHead}>
                    <span className={styles.bucketLabel}>{b.label}</span>
                    <span className={`num ${styles.bucketCount}`}>
                      {count}
                    </span>
                  </div>
                  <div className={styles.bucketBar}>
                    <div
                      className={styles.bucketFill}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className={`num ${styles.bucketPct}`}>
                    {pct.toFixed(0)}%
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
