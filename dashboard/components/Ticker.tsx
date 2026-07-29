"use client";

import type { BoardRow, GameDetail } from "@/lib/types";
import type { NightCounts } from "@/lib/reconcile";
import { chainText, nightFromBoard } from "@/lib/reconcile";
import styles from "./Ticker.module.css";

/**
 * Top-bar status strip.  Originally a scrolling marquee of every game's
 * matchup + lambda; that competed for attention with the main header.
 *
 * 2026-07-28 redesign (invariant I1 -- ONE VOCABULARY): the centre
 * segment used to be three zone chips whose loudest reading was
 * "6 STRONG YRFI".  Nothing else on the page used that phrase, so the
 * operator saw one night described one way up here, a different way on
 * the hero card and a third way in the season record -- and concluded
 * bets had gone missing.  The centre now renders chainText() from
 * lib/reconcile, the same string the hero card and the day view build
 * from the same counts.  Nothing here recounts anything.
 *
 * Slate context that is NOT a bet count (average lambda, game count)
 * stays -- it never took part in the confusion.
 */
export function Ticker({
  rows,
  details,
  date,
  night: nightProp,
  shown,
}: {
  rows: BoardRow[];
  /** Required on purpose.  nightFromBoard needs the ledger detail to know
   *  what was PLACED and SETTLED; defaulting it to {} would silently
   *  publish "PLACED 0 · SETTLED 0" on a night with four live bets, which
   *  is the exact failure this rewrite exists to remove. */
  details: Record<string, GameDetail>;
  date: string;
  /** THE night, resolved once in DashboardShell and shared with the hero
   *  card and the money panel so the three cannot describe different
   *  nights.  Falls back to reading the board when absent. */
  night?: NightCounts;
  /** Games currently passing the board's filters, when that differs from
   *  the full slate. */
  shown?: number;
}) {
  const avgLambda =
    rows.length === 0
      ? 0
      : rows.reduce((a, r) => a + r.lambda, 0) / rows.length;

  const night = nightProp ?? nightFromBoard(rows, details, date);

  return (
    <div className={styles.bar} aria-label="Slate ticker">
      <div className={styles.label}>
        <span className={styles.dot} />
        <span className={styles.labelText}>Live slate</span>
      </div>

      <div className={styles.summary}>
        {/* Rendered verbatim, never reworded per surface. */}
        <span className={styles.metricVal}>{chainText(night)}</span>
        <span className={styles.divider} aria-hidden />
        <span className={styles.metric}>
          <span className={styles.metricKey}>λ̄</span>
          <span className={styles.metricVal}>{avgLambda.toFixed(3)}</span>
        </span>
        <span className={styles.metric}>
          <span className={styles.metricKey}>games</span>
          <span className={styles.metricVal}>
            {shown != null && shown !== rows.length
              ? `${shown}/${rows.length}`
              : rows.length}
          </span>
        </span>
      </div>

      <div className={styles.rightCap}>
        {date ? date.replace(/-/g, ".") : "—"}
      </div>
    </div>
  );
}
