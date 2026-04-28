"use client";

import type { BoardRow, GameDetail } from "@/lib/types";
import styles from "./SlateProjections.module.css";

/**
 * Slate Projections panel — expected first-inning runs per half + the
 * combined total, in a Bloomberg-terminal-style read-out.
 *
 * Row anatomy (left -> right):
 *   [zone stripe] | matchup | HEADLINE total | home pitcher | T1 input | away pitcher | B1 input
 *
 * The headline is the 28px mono number colored by zone (no pill).
 * The half-inning inputs are smaller mono digits colored by tier (no pill).
 * Pitcher names are 15px Geist Sans 600 -- co-dominant with the headline.
 * STRONG NRFI (left edge: phosphor green) and STRONG YRFI (signal red)
 * stripe the row visually; PASS rows get no stripe.
 *
 * Reading guide:
 *   total <= 0.55  ->  STRONG NRFI candidate
 *   total >= 1.05  ->  STRONG YRFI candidate
 */

type Zone = "NRFI_STRONG" | "PASS" | "YRFI_STRONG";

export function SlateProjections({
  rows,
  details,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
}) {
  // Sort by combined lambda ascending (best NRFI -> best YRFI).
  const sorted = [...rows].sort((a, b) => {
    const da = details[a.gamePk || `${a.away}@${a.home}`];
    const db = details[b.gamePk || `${b.away}@${b.home}`];
    const la = da?.lambdaLrTotal ?? 99;
    const lb = db?.lambdaLrTotal ?? 99;
    return la - lb;
  });

  // Pre-compute zone for each visible row so we can insert group separators
  // when the zone changes between adjacent rows.
  const visible = sorted
    .map((row) => {
      const key = row.gamePk || `${row.away}@${row.home}`;
      const d = details[key];
      if (!d) return null;
      return { row, key, d, zone: rowZone(row) };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Slate Projections</span>
        <span className={styles.title}>Expected first-inning runs</span>
        <span className={styles.legend}>
          <span className={styles.legendKey} data-tone="nrfi">
            ≤ 0.55
          </span>
          <span className={styles.legendLabel}>strong NRFI</span>
          <span className={styles.legendDot}>·</span>
          <span className={styles.legendKey} data-tone="yrfi">
            ≥ 1.05
          </span>
          <span className={styles.legendLabel}>strong YRFI</span>
        </span>
      </header>

      <div className={styles.tableWrap}>
        {/* Spectrum thread: a 1px gradient at the top of the card that
            represents the NRFI -> YRFI axis at a single glance.  This is
            the panel's identity; legend is built into the chrome. */}
        <div className={styles.spectrum} aria-hidden="true" />

        <div className={styles.thead}>
          <div className={styles.col_match}>Matchup</div>
          <div className={`${styles.col_total} ${styles.right}`}>Projected runs</div>
          <div className={styles.col_pname}>Home pitcher</div>
          <div className={`${styles.col_phalf} ${styles.right}`}>Top 1st proj</div>
          <div className={styles.col_pname}>Away pitcher</div>
          <div className={`${styles.col_phalf} ${styles.right}`}>Bot 1st proj</div>
        </div>

        {visible.map((item, i) => {
          const prev = i > 0 ? visible[i - 1].zone : null;
          const startsNewZone = prev !== null && prev !== item.zone;

          const { row, key, d, zone } = item;
          const total    = d.lambdaLrTotal;
          const t1       = d.lambdaLrT1;
          const b1       = d.lambdaLrB1;
          const homeName = d.home?.pitcher?.name?.trim() || "TBD";
          const awayName = d.away?.pitcher?.name?.trim() || "TBD";
          const homeTbd  = isTbd(homeName);
          const awayTbd  = isTbd(awayName);

          return (
            <div
              key={key}
              className={[
                styles.row,
                styles[`row_${zone}`],
                startsNewZone ? styles.row_zoneBreak : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              {/* Left zone stripe — colored only on STRONG rows */}
              <span className={styles.stripe} aria-hidden="true" />

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
                    ? `top ${(t1 ?? 0).toFixed(2)} + bot ${(b1 ?? 0).toFixed(2)}`
                    : ""
                }
              >
                {fmt(total)}
              </div>

              <div className={styles.col_pname}>
                {homeTbd ? (
                  <span className={styles.tbd}>TBD</span>
                ) : (
                  <span className={styles.pname}>{homeName}</span>
                )}
              </div>
              <div
                className={`${styles.col_phalf} ${styles.right} ${
                  homeTbd ? styles.tone_dim : styles[halfTone(t1)]
                }`}
              >
                {fmt(t1)}
              </div>

              <div className={styles.col_pname}>
                {awayTbd ? (
                  <span className={styles.tbd}>TBD</span>
                ) : (
                  <span className={styles.pname}>{awayName}</span>
                )}
              </div>
              <div
                className={`${styles.col_phalf} ${styles.right} ${
                  awayTbd ? styles.tone_dim : styles[halfTone(b1)]
                }`}
              >
                {fmt(b1)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ---------- formatting + tone helpers ---------- */

/** Two-decimal projection.  "—" for null/NaN so the grid stays aligned. */
function fmt(n: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

function isTbd(name: string): boolean {
  return !name || name.toUpperCase() === "TBD";
}

/**
 * Row's pick zone for the left stripe + group separator.  Driven by the
 * predictor's pickSide + pickStrength on BoardRow (set by classify_pick_lr
 * in mlb_first_inning_predictor.py with thresholds 0.58 / 0.42).
 */
function rowZone(row: BoardRow): Zone {
  if (row.pickSide === "NRFI" && row.pickStrength === "STRONG") return "NRFI_STRONG";
  if (row.pickSide === "YRFI" && row.pickStrength === "STRONG") return "YRFI_STRONG";
  return "PASS";
}

/** Color bucket for the combined first-inning lambda (the headline). */
function totalTone(n: number | null): string {
  if (n == null) return "tone_neutral";
  if (n <= 0.55) return "tone_nrfi_strong";
  if (n <= 0.75) return "tone_nrfi_lean";
  if (n <  0.95) return "tone_neutral";
  if (n <  1.05) return "tone_yrfi_lean";
  return "tone_yrfi_strong";
}

/** Color bucket for one half-inning's projected runs. */
function halfTone(n: number | null): string {
  if (n == null) return "tone_neutral";
  if (n <= 0.30) return "tone_nrfi_strong";
  if (n <= 0.40) return "tone_nrfi_lean";
  if (n <  0.50) return "tone_neutral";
  if (n <  0.60) return "tone_yrfi_lean";
  return "tone_yrfi_strong";
}
