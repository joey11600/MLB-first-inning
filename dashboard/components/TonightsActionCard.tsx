"use client";

/**
 * TonightsActionCard -- the top-of-fold "what do I do tonight" surface.
 *
 * Sits directly under the header in DashboardShell.  Tells the operator at
 * a glance: how many games the model FLAGGED tonight, how many of those
 * actually carry a real bet, how many have settled, and what that has cost
 * or made so far.
 *
 * 2026-07-28 redesign (invariants I1 ONE VOCABULARY / I2 ONE SOURCE).
 * This card used to derive its own headline by counting STRONG rows with
 * a NRFI/YRFI side, while the ticker counted a different way and the
 * season record counted a third way.  Three defensible counts, three
 * different numbers for one night, nothing on screen explaining why --
 * which read to the operator as "my bets are missing".  The headline and
 * the reconcile line below it now both come from ONE call to
 * nightFromBoard(); this file no longer counts bets at all.
 *
 * A COUNT IS NOT MONEY.  The headline renders in --foreground and is never
 * tone-coloured -- peach/rust are reserved for figures that are real money
 * (invariant I4).  Only the stake and the settled P&L carry tone.
 *
 * Reads from rows + details that DashboardShell already receives from the
 * BoardResponse -- no new data layer.
 *
 * Empty-slate behavior: when nothing is flagged and nothing is pending,
 * the card collapses into a calmer treatment so it doesn't shout when
 * there is nothing to do.
 */

import type { BoardRow, GameDetail } from "@/lib/types";
import { fmtU, nightFromBoard } from "@/lib/reconcile";
import styles from "./TonightsActionCard.module.css";
// .reconLine / .reconSep are defined ONCE, in RoiPanel.module.css, and
// deliberately shared: the reconcile sentence must look identical here and
// inside the money panel or it stops reading as the same statement.
import money from "./RoiPanel.module.css";

interface TonightsActionCardProps {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  /** Slate date, only used for the eyebrow.  Optional because
   *  DashboardShell does not pass it yet; the counts do not depend on it. */
  date?: string;
}

interface SideBreakdown {
  count:    number;
  /** Units actually committed on this side (sum of the ledger's stakes). */
  unitsAt:  number;
}

interface SlateSides {
  nrfi:       SideBreakdown;
  yrfi:       SideBreakdown;
  pending:    number;          // count of LINEUP/STARTER PENDING rows
  unitsTotal: number;          // sum across both sides
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

/**
 * Side split + stake only.  The flagged / placed / settled counts are NOT
 * computed here -- they come from lib/reconcile so that every surface on
 * the page moves together.
 */
function summarizeSides(
  rows: BoardRow[],
  details: Record<string, GameDetail>,
): SlateSides {
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

  return {
    nrfi:       { count: nrfiCount, unitsAt: nrfiUnits },
    yrfi:       { count: yrfiCount, unitsAt: yrfiUnits },
    pending,
    unitsTotal: nrfiUnits + yrfiUnits,
  };
}

export function TonightsActionCard({
  rows,
  details = {},
  date,
}: TonightsActionCardProps) {
  const s = summarizeSides(rows, details);
  // THE single source for flagged / placed / settled / ledger P&L.
  const night = nightFromBoard(rows, details, date ?? "");

  // Empty state: nothing flagged AND nothing pending -- the slate is
  // either fully PASS or already graded.  Calmer treatment, smaller card,
  // no jump links, no urgent dot.
  if (night.flagged === 0 && s.pending === 0) {
    return (
      <section className={`${styles.wrap} ${styles.empty}`} aria-label="Tonight's action">
        <div className={styles.emptyInner}>
          <span className={styles.emptyEyebrow}>
            {date ? `Tonight · ${date}` : "Tonight"}
          </span>
          <span className={styles.emptyMain}>No games flagged tonight.</span>
          <span className={styles.emptySub}>
            Model declined every game -- nothing to bet.
          </span>
        </div>
      </section>
    );
  }

  // Games the model flagged but that carry no real bet.  Stated plainly
  // rather than left for the operator to subtract: an unexplained gap
  // between two counts is what started this whole redesign.
  const notPlaced = night.flagged - night.placed;

  return (
    <section className={styles.wrap} aria-label="Tonight's action">
      <div className={styles.head}>
        {/* Global .eyebrow, not the local copy: TonightsActionCard.module.css
            drops its own reimplementation in this pass so all eyebrows on
            the page share one definition. */}
        <span className="eyebrow">
          {date ? `Tonight · ${date}` : "Tonight"}
        </span>
        <span className={styles.subtitle}>
          {night.flagged > 0
            ? "Strong plays on the slate"
            : "Lineups still pending — leans incoming"}
        </span>
      </div>

      <div className={styles.body}>
        <div className={styles.heroBlock}>
          {/* A count, not money: --foreground, never tone-coloured. */}
          <span className={styles.heroNum}>{night.flagged}</span>
          <span className={styles.heroUnit}>flagged STRONG</span>
          {s.unitsTotal > 0 && (
            <span className={styles.heroStake}>
              {s.unitsTotal.toFixed(2)}u at risk
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

      {/* The reconcile line.  Same four numbers, same order, same source as
          the ticker above and the day view below. */}
      <p className={money.reconLine}>
        <span>
          <b>{night.flagged}</b> flagged
        </span>
        <span className={money.reconSep} aria-hidden>·</span>
        <span>
          <b>{night.placed}</b> placed
        </span>
        <span className={money.reconSep} aria-hidden>·</span>
        <span>
          <b>{night.settled}</b> settled <b>{fmtU(night.ledgerPL)}</b>
        </span>
      </p>

      {notPlaced > 0 && (
        <p className={money.reconLine}>
          <span>
            <b>{notPlaced}</b> flagged but not placed
          </span>
        </p>
      )}

      {/* Plain-English legend.  The three counts are ALLOWED to differ;
          saying so out loud is cheaper than another "my bets vanished"
          incident. */}
      <p className="meta">
        Flagged = the model called it STRONG. Placed = a real bet is in the
        ledger. Settled = graded and paid. These three counts are allowed to
        differ; the table below shows every game once.
      </p>
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

  // Two decimals everywhere units appear, matching fmtU, so the same
  // quantity never shows up as "4.0u" here and "4.00u" ten pixels away.
  const meta = units > 0 ? `${units.toFixed(2)}u` : "";

  return (
    <div className={styles.sideRow}>
      <span className={styles.sideDot} data-tone={tone} aria-hidden />
      <span className={styles.sideLabel}>{label}</span>
      <span className={`num ${styles.sideCount}`}>{count}</span>
      {meta && <span className={styles.sideMeta}>{meta}</span>}
    </div>
  );
}
