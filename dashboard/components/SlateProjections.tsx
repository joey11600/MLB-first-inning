"use client";

import type { BoardRow, GameDetail } from "@/lib/types";
import styles from "./SlateProjections.module.css";

/**
 * Slate Projections panel — shows expected first-inning runs per half plus
 * the combined total, formatted like the user's friend's reference table.
 *
 * Reading guide (from user):
 *   total ~ 0.5  ->  good NRFI
 *   total ~ 1.0  ->  good YRFI
 *
 * Each half-inning column shows the pitcher's expected runs allowed in
 * THEIR half: home pitcher pitches T1, away pitcher pitches B1.
 */

export function SlateProjections({
  rows,
  details,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
}) {
  // Sort by combined lambda ascending (best NRFI -> best YRFI)
  const sorted = [...rows].sort((a, b) => {
    const da = details[a.gamePk || `${a.away}@${a.home}`];
    const db = details[b.gamePk || `${b.away}@${b.home}`];
    const la = da?.lambdaLrTotal ?? 99;
    const lb = db?.lambdaLrTotal ?? 99;
    return la - lb;
  });

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Slate Projections</span>
        <span className={styles.title}>Expected first-inning runs</span>
        <span className={styles.legend}>
          <span className={styles.legendKey} data-tone="nrfi">≤ 0.5</span>
          good NRFI
          <span className={styles.legendDot}>·</span>
          <span className={styles.legendKey} data-tone="yrfi">≥ 1.0</span>
          good YRFI
        </span>
      </header>

      <div className={styles.tableWrap}>
        <div className={styles.thead}>
          <div className={styles.col_match}>Matchup</div>
          <div className={`${styles.col_total} ${styles.right}`}>
            Projected runs <span className={styles.thsub}>1st inning</span>
          </div>
          <div className={styles.col_pname}>Home pitcher</div>
          <div className={`${styles.col_phalf} ${styles.right}`}>
            ½ inning <span className={styles.thsub}>proj</span>
          </div>
          <div className={styles.col_pname}>Away pitcher</div>
          <div className={`${styles.col_phalf} ${styles.right}`}>
            ½ inning <span className={styles.thsub}>proj</span>
          </div>
        </div>

        {sorted.map((row) => {
          const key = row.gamePk || `${row.away}@${row.home}`;
          const d = details[key];
          if (!d) return null;
          const total = d.lambdaLrTotal;
          const t1    = d.lambdaLrT1;
          const b1    = d.lambdaLrB1;
          const homeName = d.home?.pitcher?.name || "TBD";
          const awayName = d.away?.pitcher?.name || "TBD";

          return (
            <div key={key} className={styles.row}>
              <div className={styles.col_match}>
                <span className={styles.team}>{row.away}</span>
                <span className={styles.at}>@</span>
                <span className={styles.team}>{row.home}</span>
                {row.doubleHeader && row.doubleHeader !== "N" && (
                  <span className={styles.dhTag}>DH-{row.gameNumber}</span>
                )}
              </div>
              <div
                className={`${styles.col_total} ${styles.right} ${styles[totalTone(total)]}`}
                title={
                  total !== null
                    ? `home ${(t1 ?? 0).toFixed(3)} + away ${(b1 ?? 0).toFixed(3)}`
                    : ""
                }
              >
                {fmt(total)}
              </div>
              <div className={styles.col_pname}>{homeName}</div>
              <div className={`${styles.col_phalf} ${styles.right} ${styles[halfTone(t1)]}`}>
                {fmt(t1)}
              </div>
              <div className={styles.col_pname}>{awayName}</div>
              <div className={`${styles.col_phalf} ${styles.right} ${styles[halfTone(b1)]}`}>
                {fmt(b1)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function fmt(n: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(3);
}

/** Color bucket for the combined first-inning lambda. */
function totalTone(n: number | null): string {
  if (n == null) return "tone_neutral";
  if (n <= 0.50) return "tone_nrfi_strong";
  if (n <= 0.70) return "tone_nrfi_lean";
  if (n <  0.90) return "tone_neutral";
  if (n <  1.10) return "tone_yrfi_lean";
  return "tone_yrfi_strong";
}

/** Color bucket for one half-inning's projected runs.  Lower is better
 * for the NRFI side; higher means that pitcher is likely to give up runs. */
function halfTone(n: number | null): string {
  if (n == null) return "tone_neutral";
  if (n <= 0.30) return "tone_nrfi_strong";
  if (n <= 0.40) return "tone_nrfi_lean";
  if (n <  0.50) return "tone_neutral";
  if (n <  0.60) return "tone_yrfi_lean";
  return "tone_yrfi_strong";
}
