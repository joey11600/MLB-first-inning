"use client";

/**
 * TonightsActionCard -- the new top-of-fold "what do I do tonight" surface.
 *
 * Sits above SummaryStrip in DashboardShell.  Tells the operator at a
 * glance: how many STRONG plays are on the slate, the side breakdown,
 * the unit stake committed, and how many picks are still pending lineups.
 *
 * Reads from rows + details that DashboardShell already receives from
 * the BoardResponse -- no new data layer.
 *
 * Empty-slate behavior: when there are zero STRONG plays, the card
 * collapses into a calmer "slate locked, nothing to bet" treatment so
 * it doesn't shout at the user when there's nothing to do.
 *
 * Aesthetic direction: refined Diamond Terminal -- mono hero numerals,
 * muted eyebrows, tone-tinted accent bars, no chip soup.  Matches the
 * SummaryStrip tile family but lives one level above it in the
 * hierarchy (4-up summary tiles are retrospective; this card is
 * prospective).
 */

import type { BoardRow, GameDetail } from "@/lib/types";
import styles from "./TonightsActionCard.module.css";

interface TonightsActionCardProps {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
}

interface SideBreakdown {
  count:    number;
  /** Total units committed (sum of bet_placed='Y' rows × 1u flat). */
  unitsAt:  number;
}

interface ActionSummary {
  total:        number;          // total STRONG plays
  nrfi:         SideBreakdown;
  yrfi:         SideBreakdown;
  pending:      number;          // count of LINEUP/STARTER PENDING rows
  unitsTotal:   number;          // sum across both sides
  /** True when there's zero meaningful action -- card flips to empty state. */
  empty:        boolean;
}

function lookupDetail(
  r: BoardRow,
  details: Record<string, GameDetail>,
): GameDetail | undefined {
  return (
    (r.gamePk && details[r.gamePk]) ||
    details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
    details[`${r.away}@${r.home}`]
  );
}

function summarize(
  rows: BoardRow[],
  details: Record<string, GameDetail>,
): ActionSummary {
  let nrfiCount   = 0;
  let yrfiCount   = 0;
  let nrfiUnits   = 0;
  let yrfiUnits   = 0;
  let pending     = 0;

  for (const r of rows) {
    const strength = r.pickStrength;

    if (strength === "LINEUP PENDING" || strength === "STARTER PENDING") {
      pending += 1;
      continue;
    }

    if (strength !== "STRONG") continue;

    const d      = lookupDetail(r, details);
    const placed = d?.betPlaced;
    // 2026-07-28 P0 FIX (code audit): this was hard-coded to 1u per
    // placed bet while quarter-Kelly is live and staking ~4-10u -- the
    // hero card was understating tonight's real exposure severalfold.
    // Use the ledger's recorded stake; a placed row with no recorded
    // stake yet contributes 0 rather than an invented figure (same
    // no-guess rule as the StakeChip).
    const stakeU = placed === "Y" && d?.unitsRisked != null ? d.unitsRisked : 0;

    if (r.pickSide === "NRFI") {
      nrfiCount += 1;
      nrfiUnits += stakeU;
    } else if (r.pickSide === "YRFI") {
      yrfiCount += 1;
      yrfiUnits += stakeU;
    }
  }

  const total      = nrfiCount + yrfiCount;
  const unitsTotal = nrfiUnits + yrfiUnits;
  const empty      = total === 0 && pending === 0;

  return {
    total,
    nrfi:    { count: nrfiCount, unitsAt: nrfiUnits },
    yrfi:    { count: yrfiCount, unitsAt: yrfiUnits },
    pending,
    unitsTotal,
    empty,
  };
}

export function TonightsActionCard({
  rows,
  details = {},
}: TonightsActionCardProps) {
  const s = summarize(rows, details);

  // Empty state: no STRONG plays AND nothing pending -- slate is
  // either fully PASS or already-graded.  Calmer treatment, smaller
  // card, no jump links, no urgent dot.
  if (s.empty) {
    return (
      <section className={`${styles.wrap} ${styles.empty}`} aria-label="Tonight's action">
        <div className={styles.emptyInner}>
          <span className={styles.emptyEyebrow}>Tonight</span>
          <span className={styles.emptyMain}>No plays on the slate</span>
          <span className={styles.emptySub}>
            Model declined every game -- nothing to bet.
          </span>
        </div>
      </section>
    );
  }

  // Active state: at least one STRONG or one pending row.  Hero number
  // shows total STRONG count; pending count surfaces below as a
  // secondary signal so the operator knows more bets may resolve once
  // lineups post.
  return (
    <section className={styles.wrap} aria-label="Tonight's action">
      <div className={styles.head}>
        <span className={styles.eyebrow}>Tonight</span>
        <span className={styles.subtitle}>
          {s.total > 0
            ? "Strong plays on the slate"
            : "Lineups still pending — leans incoming"}
        </span>
      </div>

      <div className={styles.body}>
        <div className={styles.heroBlock}>
          <span className={styles.heroNum}>{s.total}</span>
          <span className={styles.heroUnit}>
            {s.total === 1 ? "PLAY" : "PLAYS"}
          </span>
          {s.unitsTotal > 0 && (
            <span className={styles.heroStake}>
              {s.unitsTotal.toFixed(1)}u staked
            </span>
          )}
        </div>

        <div className={styles.divider} aria-hidden />

        <div className={styles.sideStack}>
          <SideRow
            label="NRFI"
            tone="nrfi"
            count={s.nrfi.count}
            units={s.nrfi.unitsAt}
          />
          <SideRow
            label="YRFI"
            tone="yrfi"
            count={s.yrfi.count}
            units={s.yrfi.unitsAt}
          />
          {s.pending > 0 && (
            <div className={`${styles.sideRow} ${styles.sidePending}`}>
              <span className={styles.sideDot} data-tone="pending" aria-hidden />
              <span className={styles.sideLabel}>Pending</span>
              <span className={`num ${styles.sideCount}`}>{s.pending}</span>
              <span className={styles.sideMeta}>lineup not posted</span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function SideRow({
  label,
  tone,
  count,
  units,
}: {
  label: "NRFI" | "YRFI";
  tone:  "nrfi" | "yrfi";
  count: number;
  units: number;
}) {
  if (count === 0) {
    return (
      <div className={`${styles.sideRow} ${styles.sideEmpty}`}>
        <span className={styles.sideDot} data-tone={tone} aria-hidden />
        <span className={styles.sideLabel}>{label}</span>
        <span className={`num ${styles.sideCount}`}>0</span>
        <span className={styles.sideMeta}>no plays</span>
      </div>
    );
  }

  const meta = units > 0 ? `${units.toFixed(1)}u` : "";

  return (
    <div className={styles.sideRow}>
      <span className={styles.sideDot} data-tone={tone} aria-hidden />
      <span className={styles.sideLabel}>{label}</span>
      <span className={`num ${styles.sideCount}`}>{count}</span>
      {meta && <span className={styles.sideMeta}>{meta}</span>}
    </div>
  );
}
