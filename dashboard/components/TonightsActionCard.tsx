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
 * use -- LEDGER FIRST, replay second (corrected 2026-07-30; it was the
 * other way round, which made the board claim "staked 17.00u" on a bet
 * actually placed at 5.97u).  Adding up the chips has to reproduce the
 * number in this card.
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
import { stakeUnitsFor } from "@/lib/kelly-sim";
import {
  computeLockAt, formatLockTime, minutesUntil, formatCountdown,
} from "@/lib/lock";
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

/** ONE PLAY THE OPERATOR HAS TO ACT ON.
 *
 *  2026-07-29 decision-first redesign.  This card counted things --
 *  "2 flagged STRONG", a NRFI/YRFI split, a passed tally -- and never
 *  once said WHICH GAMES TO BET.  The operator's actual question, per
 *  PRODUCT.md, is "what do I bet tonight, and how much?", asked with a
 *  phone in one hand in the hour before first pitch.  Answering it
 *  required scrolling past the performance panel to a 16-row table and
 *  picking the STRONG rows out by eye.
 *
 *  A count is a summary of the answer, not the answer. */
interface Play {
  key:      string;
  away:     string;
  home:     string;
  timeEt:   string;
  side:     "NRFI" | "YRFI";
  /** Units to stake.  Locked rows show the ledger's frozen figure; live
   *  rows show what the model is currently sizing. */
  units:    number | null;
  /** American price on the picked side, e.g. "-145". */
  price:    string;
  /** Already committed to the ledger (bet_placed=Y). */
  locked:   boolean;
  /** Graded terminal state, if the first inning is already over. */
  graded:   string;
  /** Minutes until the T2.58 lock window commits this pick.  Null when
   *  the game time is a placeholder, or the row is already locked. */
  locksInMin: number | null;
  locksAtLabel: string;
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

/** The STRONG picks, as things to act on rather than things to count.
 *
 *  Stake resolution is IDENTICAL to summarizeSides below and to
 *  BoardRow's StakeChip -- LEDGER FIRST, replay second -- because the
 *  three are the same quantity shown in three places and any drift
 *  between them reads as the system contradicting itself.
 *
 *  Sorted by lock deadline, soonest first: the ordering the operator
 *  actually needs is "what closes next", not board rank.  Already-locked
 *  and graded plays sink to the bottom -- there is nothing left to do
 *  about them. */
function extractPlays(
  rows: BoardRow[],
  details: Record<string, GameDetail>,
  slateDate: string,
  replayStakes?: Map<string, ReplayStake>,
): Play[] {
  const now = new Date();
  const out: Play[] = [];

  for (const r of rows) {
    if (r.pickStrength !== "STRONG") continue;
    if (r.pickSide !== "NRFI" && r.pickSide !== "YRFI") continue;

    const d = lookupDetail(r, details);
    const locked = d?.betPlaced === "Y";
    const graded = (d?.gradedResult || "").trim();

    const rp = replayStakes && (
      replayStakes.get(replayKey(r.away, r.home, r.gameNumber, r.pickSide)) ??
      replayStakes.get(replayKey(r.away, r.home, r.gameNumber, "YRFI")) ??
      replayStakes.get(replayKey(r.away, r.home, r.gameNumber, "NRFI"))
    );
    // THE NEW SYSTEM'S STAKE (2026-07-30), computed from the model
    // probability and the price. 1 unit = 1% of bankroll, so this is
    // the same number for every subscriber and the same number
    // /history shows. Reading d.unitsRisked here would show the OLD
    // ledger sizing on anything placed before today.
    const price = (r.pickSide === "NRFI" ? d?.marketNrfiOdds : d?.marketYrfiOdds) || "";
    const american = Number.parseFloat(price.trim());
    const modelP = (r.pickSide === "NRFI" ? r.nrfiPct : r.yrfiPct) / 100;
    let units: number | null =
      Number.isFinite(american) && american !== 0
        ? stakeUnitsFor(modelP, american)
        : null;
    if (units == null && rp?.action === "BET" && typeof rp.stake === "number") {
      units = rp.stake;
    }

    const lockAt = computeLockAt(r.gameTimeEt, slateDate);
    const mins = lockAt && !locked && !graded ? minutesUntil(lockAt, now) : null;

    out.push({
      key: `${r.gamePk || `${r.away}@${r.home}`}#${r.gameNumber || 1}`,
      away: r.away,
      home: r.home,
      timeEt: r.gameTimeEt,
      side: r.pickSide,
      units,
      price,
      locked,
      graded,
      locksInMin: mins,
      locksAtLabel: lockAt ? formatLockTime(lockAt) : "",
    });
  }

  const weight = (p: Play) => (p.graded ? 2 : p.locked ? 1 : 0);
  return out.sort((a, b) => {
    const w = weight(a) - weight(b);
    if (w !== 0) return w;
    const am = a.locksInMin ?? Number.MAX_SAFE_INTEGER;
    const bm = b.locksInMin ?? Number.MAX_SAFE_INTEGER;
    return am - bm;
  });
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

    // Same precedence as the play list and the board chip: the stake
    // the operator actually has on the game, then the replay. This
    // total is captioned "at risk", so it must be the real exposure.
    // Same rule as the play list above and the board chip.
    let stakeU = 0;
    const priceRaw = (r.pickSide === "NRFI" ? d?.marketNrfiOdds : d?.marketYrfiOdds) || "";
    const am = Number.parseFloat(priceRaw.trim());
    const mp = (r.pickSide === "NRFI" ? r.nrfiPct : r.yrfiPct) / 100;
    if (Number.isFinite(am) && am !== 0) {
      stakeU = stakeUnitsFor(mp, am);
    } else if (rp?.action === "BET" && typeof rp.stake === "number") {
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
  const plays = extractPlays(rows, details, date ?? "", replayStakes);
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

      {/* THE ANSWER, FIRST.  Everything below this list is context for
          it. See the Play interface for why a list replaced a count. */}
      {plays.length > 0 && (
        <ul className={styles.playList}>
          {plays.map((p) => (
            <li
              key={p.key}
              className={styles.play}
              data-state={p.graded ? "graded" : p.locked ? "locked" : "open"}
            >
              <span className={styles.playMatchup}>
                <span className={styles.playTeams}>
                  {p.away} <span className={styles.playAt}>at</span> {p.home}
                </span>
                <span className={styles.playTime}>{p.timeEt}</span>
              </span>

              <span className={styles.playCall}>
                <span className={styles.playSide} data-side={p.side}>{p.side}</span>
                {p.units != null && p.units > 0 && (
                  <span className={`num ${styles.playStake}`}>
                    {p.units.toFixed(p.units % 1 === 0 ? 0 : 2)}u
                  </span>
                )}
                {p.price && <span className={`num ${styles.playPrice}`}>{p.price}</span>}
              </span>

              <span className={styles.playState}>
                {p.graded ? (
                  <span className={styles.playGraded} data-result={p.graded}>
                    {p.graded}
                  </span>
                ) : p.locked ? (
                  <span className={styles.playLocked}>bet placed</span>
                ) : p.locksInMin != null ? (
                  // The deadline. --attn per the colour law: this is the
                  // one thing on the page waiting on a decision.
                  <span className={styles.playLocks} data-soon={p.locksInMin <= 45 ? "1" : undefined}>
                    locks {formatCountdown(p.locksInMin)}
                    <span className={styles.playLocksAt}>{p.locksAtLabel}</span>
                  </span>
                ) : (
                  <span className={styles.playLocked}>awaiting lineup</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {s.unitsTotal > 0 && (
        <p className={styles.exposure}>
          <span className={styles.exposureFig}>{s.unitsTotal.toFixed(2)}u</span>
          <span className={styles.exposureLabel}>
            at risk across {night.flagged}{" "}
            {night.flagged === 1 ? "play" : "plays"}
          </span>
        </p>
      )}

      <div className={styles.body}>
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
          incident.

          2026-07-29 distill pass: the words are unchanged, but they no
          longer sit open on the page. This is a DEFINITION, not a
          finding -- it is identical every night, so after the second
          reading it is furniture the eye has to step over to reach the
          board. Collapsed, it stays one tap away on the exact surface
          where the question arises. */}
      <details className={styles.legend}>
        <summary className={styles.legendSummary}>
          What do flagged, placed and settled mean?
        </summary>
        <p className="meta">
          Flagged = the model called it STRONG. Placed = a real bet is in the
          ledger. Settled = graded and paid. These three counts are allowed to
          differ; the table below shows every game once.
        </p>
      </details>
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
