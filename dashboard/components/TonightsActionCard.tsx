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
 * COLOUR RULE (2026-07-28 redesign).  A hue means real money:
 *   peach = money up, rust = money down, amber = money at risk or a
 *   decision waiting on you.  So:
 *     - the headline is a COUNT and renders in --foreground, never toned;
 *     - "{n}u at risk" is EXPOSURE, not profit, so it is AMBER, not peach.
 *       That one figure is the clearest demonstration of the rule on the
 *       whole page: it is real money, but it has not won or lost yet;
 *     - the NRFI / YRFI dots carry ink WEIGHT, not hue, because a peach
 *       dot used to mean either "NRFI" or "profit" and nothing on screen
 *       said which.
 *
 * THE AT-RISK FIGURE IS THE SUM OF THE VISIBLE STAKE CHIPS.  It resolves
 * each game's stake through the exact same precedence the board's chips
 * use (replay first, ledger second) -- see summarizeSides below.  Adding
 * up the chips has to reproduce the number in this card.
 *
 * Reads from rows + details that DashboardShell already receives from the
 * BoardResponse -- no new data layer.
 *
 * Empty-slate behavior: when nothing is flagged and nothing is pending,
 * the card collapses into a calmer treatment so it doesn't shout when
 * there is nothing to do.
 */

import type { BoardRow, GameDetail } from "@/lib/types";
import type { NightCounts } from "@/lib/reconcile";
import { fmtU, nightFromBoard } from "@/lib/reconcile";
import type { ReplayStake } from "@/lib/season-record";
import { replayKey } from "@/lib/season-record";
import styles from "./TonightsActionCard.module.css";
// .reconLine / .reconSep are defined ONCE, in RoiPanel.module.css, and
// deliberately shared: the reconcile sentence must look identical here and
// inside the money panel or it stops reading as the same statement.
import money from "./RoiPanel.module.css";

interface TonightsActionCardProps {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  /** Slate date.  Shown in the eyebrow, and used to build `night` when the
   *  shell has not resolved one (it always does now). */
  date?: string;
  /** THE night object, resolved once in DashboardShell so this card, the
   *  ticker and the money panel cannot describe different nights.  Falls
   *  back to reading the board directly if it is not supplied. */
  night?: NightCounts;
  /** What the CURRENT model stakes per game, keyed the same way the board's
   *  stake chips key it.  Passing this is what makes the "at risk" figure
   *  the sum of the chips the operator can actually see. */
  replayStakes?: Map<string, ReplayStake>;
}

interface SideBreakdown {
  count:    number;
  /** Units committed on this side -- the same figure the row's stake chip
   *  prints, summed. */
  unitsAt:  number;
}

interface SlateSides {
  nrfi:       SideBreakdown;
  yrfi:       SideBreakdown;
  pending:    number;          // count of LINEUP/STARTER PENDING rows
  passed:     number;          // games the model declined outright
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
  replayStakes?: Map<string, ReplayStake>,
): SlateSides {
  let nrfiCount   = 0;
  let yrfiCount   = 0;
  let nrfiUnits   = 0;
  let yrfiUnits   = 0;
  let pending     = 0;
  let passed      = 0;

  for (const r of rows) {
    const strength = r.pickStrength;

    if (strength === "LINEUP PENDING" || strength === "STARTER PENDING") {
      pending += 1;
      continue;
    }

    if (strength !== "STRONG") {
      // Everything the model did not commit to.  A LEAN with a side is a
      // call, not a pass -- it stays off this count and appears on the
      // board on its own row.  Pending rows already left the loop above,
      // so the three counts in the side stack never double-count a game.
      if (r.pickSide === "PASS" || strength !== "LEAN") passed += 1;
      continue;
    }

    const d      = lookupDetail(r, details);
    const placed = d?.betPlaced;

    // THE STAKE, RESOLVED EXACTLY AS THE ROW'S STAKE CHIP RESOLVES IT
    // (BoardRow.tsx StakeChip).  Adding up the chips on the board has to
    // reproduce this card's total, or the operator sees one exposure up
    // here and a different one twelve rows down.
    //
    // Order matters and is NOT arbitrary: the replay's figure wins,
    // because the ledger's `unitsRisked` is a flat 1.00 on every bet
    // placed before Kelly sizing went live -- reading the ledger first
    // would make every April slate report "1.00u at risk" per bet.
    // Same reason the chips read the replay first.
    const rp = replayStakes && (
      replayStakes.get(replayKey(r.away, r.home, r.gameNumber, r.pickSide)) ??
      replayStakes.get(replayKey(r.away, r.home, r.gameNumber, "YRFI")) ??
      replayStakes.get(replayKey(r.away, r.home, r.gameNumber, "NRFI"))
    );

    let stakeU = 0;
    if (rp?.action === "BET" && typeof rp.stake === "number") {
      stakeU = rp.stake;
    } else if (rp?.action === "SKIP") {
      // The current model declined this game -- the chip says "model
      // passes" and prints no figure, so nothing is added here either.
      stakeU = 0;
    } else if (placed === "Y" && d?.unitsRisked != null) {
      // Not replayed yet, i.e. tonight.  A locked row's recorded stake IS
      // the live Kelly figure, so it is authoritative.  A placed row with
      // no recorded stake contributes 0 rather than an invented number.
      stakeU = d.unitsRisked;
    }

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
    passed,
    unitsTotal: nrfiUnits + yrfiUnits,
  };
}

export function TonightsActionCard({
  rows,
  details = {},
  date,
  night: nightProp,
  replayStakes,
}: TonightsActionCardProps) {
  const s = summarizeSides(rows, details, replayStakes);
  // THE single source for flagged / placed / settled / ledger P&L.  It is
  // resolved once in DashboardShell and handed down; the local fallback
  // only runs if this card is mounted somewhere that does not supply it.
  const night = nightProp ?? nightFromBoard(rows, details, date ?? "");

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
            Nothing to bet — the model passed on{" "}
            <span className="num">{s.passed}</span> of{" "}
            <span className="num">{rows.length}</span> games.
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
          {/* Third row, absorbing two deleted surfaces that both counted
              declined games: the money panel's "No-bet calls" chip strip
              and SummaryStrip's Pass bucket.  Kept here rather than
              dropped because the count answers "did the model look at the
              rest of the slate, or did it not run?" -- and every one of
              these games is still on the board below with a chip saying
              WHY it was passed, which is the more useful form. */}
          <div className={`${styles.sideRow} ${styles.sidePassed}`}>
            <span className={styles.sideDot} data-tone="pass" aria-hidden />
            <span className={styles.sideLabel}>Passed</span>
            <span className={`num ${styles.sideCount}`}>{s.passed}</span>
            <span className={styles.sideMeta}>model declined</span>
          </div>
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
