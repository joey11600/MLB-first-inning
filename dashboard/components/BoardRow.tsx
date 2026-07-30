"use client";

import { useEffect, useRef, useState } from "react";
import type { BatterLine, BoardRow, GameDetail, PickSide, PickStrength, PickThresholds } from "@/lib/types";
import type { LiveGameState } from "@/lib/useLiveGameState";
import { GameDetails } from "./GameDetails";
import type { ReplayStake } from "@/lib/season-record";
import { replayKey } from "@/lib/season-record";
import { computeLockAt, formatLockTime } from "@/lib/lock";
import { stakeUnitsFor } from "@/lib/kelly-sim";
import styles from "./BoardRow.module.css";

/** Returns true for `durationMs` after `value` changes.  Used by Tier 1.3
 *  to flash a row when data updates (grade lands, pick flips, P&L
 *  changes).  Initial render is NOT considered a change -- only
 *  subsequent updates trigger the pulse. */
function useRecentlyChanged<T>(value: T, durationMs: number = 2500): boolean {
  const prev = useRef<T>(value);
  const [active, setActive] = useState(false);
  useEffect(() => {
    if (Object.is(prev.current, value)) return;
    prev.current = value;
    setActive(true);
    const id = window.setTimeout(() => setActive(false), durationMs);
    return () => window.clearTimeout(id);
  }, [value, durationMs]);
  return active;
}

function toneClass(side: PickSide, strength: PickStrength): string {
  if (side === "NRFI") return strength === "STRONG" ? "nrfiStrong" : "nrfiLean";
  if (side === "YRFI") return strength === "STRONG" ? "yrfiStrong" : "yrfiLean";
  return "passTone";
}

function pickLabelText(
  side:        PickSide,
  strength:    PickStrength,
  preLock?:    boolean,
  lockTimeStr?: string,
): string {
  if (strength === "STARTER PENDING") return "STARTER PENDING";
  if (strength === "LINEUP PENDING")  return "LINEUP PENDING";
  if (strength === "LOW LAMBDA")      return "PASS · LOW λ";
  if (strength === "HIGH LAMBDA")     return "PASS · HIGH λ";
  if (strength === "FLAT ZONE")       return "PASS · FLAT";
  if (side === "PASS") return "PASS";
  // Pre-lock STRONG / LEAN -> show as PENDING with countdown.
  if (preLock && (strength === "STRONG" || strength === "LEAN")) {
    return lockTimeStr
      ? `PENDING · LOCKS ${lockTimeStr}`
      : `PENDING ${strength} ${side}`;
  }
  return `${strength} ${side}`;
}

/** True when the row should display the model's tentative lean inline.
 *  We only do this for LINEUP PENDING (pitcher data is real, only the
 *  hitters use team-fallback) -- STARTER PENDING has fallback on both
 *  sides so the tentative would be too noisy to surface as a pickable
 *  signal. */
function shouldShowTentative(strength: PickStrength): boolean {
  return strength === "LINEUP PENDING";
}

// computeLockAt / formatLockTime / etOffsetMinutes moved to lib/lock.ts
// (2026-07-29) so the decision card at the top of the page and this row
// share ONE definition of when a bet closes. Two copies of a deadline is
// two chances to disagree about it.

/** Human-readable explanation for NO DATA picks.  Pulls the specific
 *  unavailable inputs (rookie pitcher, missing offense, lineup gap)
 *  out of the detail and writes a multi-line tooltip body. */
function noDataReason(detail: GameDetail | undefined): string {
  if (!detail) return "Insufficient data — model declined to predict.";
  const issues: string[] = [];
  if (detail.away.pitcher.quality === "avg") {
    const name = detail.away.pitcher.name && detail.away.pitcher.name !== "TBD"
      ? `${detail.away.pitcher.name} (${detail.away.team})`
      : `${detail.away.team}'s starter`;
    issues.push(`${name} has insufficient MLB stats — likely a rookie debut, recent call-up, or limited sample`);
  }
  if (detail.home.pitcher.quality === "avg") {
    const name = detail.home.pitcher.name && detail.home.pitcher.name !== "TBD"
      ? `${detail.home.pitcher.name} (${detail.home.team})`
      : `${detail.home.team}'s starter`;
    issues.push(`${name} has insufficient MLB stats — likely a rookie debut, recent call-up, or limited sample`);
  }
  if (detail.away.offense.quality === "avg") {
    issues.push(`${detail.away.team} team batting data unavailable`);
  }
  if (detail.home.offense.quality === "avg") {
    issues.push(`${detail.home.team} team batting data unavailable`);
  }
  if (issues.length === 0) {
    return "Insufficient data — model declined to predict.";
  }
  return "PASS — no real prediction made because:\n• " + issues.join("\n• ");
}

/** Default classifier thresholds -- exported for GameDetails so the
 *  tentative-lean section can run the same classification as the row.
 *  Phase 1.3 (2026-05-12): LEAN tier reactivated as track-only with
 *  leanNrfiP=0.50 / leanYrfiP=0.50.  Defaults here match the values
 *  the predictor writes to data/thresholds.json on every run -- they
 *  are a fallback for the first render before the file loads. */
export const DEFAULT_THRESHOLDS: PickThresholds = {
  strongNrfiP:     1.01,  // NRFI betting disabled 2026-06-07 (1.01 = unreachable); audit fix
  leanNrfiP:       0.50,
  passLoP:         0.44,
  leanYrfiP:       0.50,
  lambdaYrfiFloor: 0.838,
  lambdaNrfiCeiling: 0.52,
  // STRONG YRFI needs pNrfi < 0.40 (0.44 -> 0.36 on 2026-07-27,
  // 0.36 -> 0.40 on 2026-07-28 after walk-forward validation).
  strongYrfiP:     0.40,
};

/** Mirror of mlb_first_inning_predictor.classify_pick_lr -- given an
 *  NRFI probability and combined lambda, returns what the model WOULD
 *  pick under the supplied thresholds (ignores LINEUP / STARTER
 *  PENDING guards).  Exported for GameDetails' tentative-lean section.
 *
 *  Phase 1.3 (2026-05-12): restructured to fire LEAN YRFI for
 *  passLoP < pNrfi < leanYrfiP when lambda clears the floor.  The
 *  previous structure short-circuited that band into PASS NO EDGE
 *  before the LEAN-YRFI check could fire. */
export function classifyTentative(
  pNrfi:       number,
  lambdaTotal: number | null,
  th:          PickThresholds | undefined,
): { side: PickSide; strength: PickStrength } {
  const t = th ?? DEFAULT_THRESHOLDS;

  if (pNrfi >= t.strongNrfiP) {
    // T1-NRFI-2026-06-01: mirror the Python NRFI lambda ceiling -- a
    // would-be STRONG NRFI with too-high projected runs is demoted to
    // PASS "HIGH LAMBDA".  Only fires inside the STRONG-NRFI branch, so
    // it cannot affect any YRFI verdict.  Older deploys omit the field;
    // skip the check when it's undefined.
    if (t.lambdaNrfiCeiling != null && lambdaTotal != null && lambdaTotal > t.lambdaNrfiCeiling) {
      return { side: "PASS", strength: "HIGH LAMBDA" };
    }
    return { side: "NRFI", strength: "STRONG" };
  }
  if (pNrfi >= t.leanNrfiP)   return { side: "NRFI", strength: "LEAN" };

  // LEAN YRFI band: strictly above passLoP, below leanNrfiP, lambda >= floor.
  if (pNrfi > t.passLoP) {
    if (lambdaTotal != null && lambdaTotal >= t.lambdaYrfiFloor) {
      return { side: "YRFI", strength: "LEAN" };
    }
    return { side: "PASS", strength: "NO EDGE" };
  }
  // p_nrfi == passLoP -- stays PASS NO EDGE to preserve the asymmetric
  // STRONG-YRFI boundary (strict <).
  if (pNrfi >= t.passLoP)     return { side: "PASS", strength: "NO EDGE" };

  // p_nrfi < passLoP -- YRFI side, gated first by the lambda floor.
  if (lambdaTotal != null && lambdaTotal < t.lambdaYrfiFloor) {
    return { side: "PASS", strength: "LOW LAMBDA" };
  }
  // 2026-07-27 (T-SELECTIVITY): only pNrfi < strongYrfiP fires STRONG.
  // The band between strongYrfiP and passLoP is a real YRFI lean but
  // does not beat the market, so it tracks as LEAN. Older deploys omit
  // the field -- fall through to the previous behaviour when undefined.
  if (t.strongYrfiP != null && pNrfi >= t.strongYrfiP) {
    return { side: "YRFI", strength: "LEAN" };
  }
  return { side: "YRFI", strength: "STRONG" };
}

function formatGameTime(s: string): string {
  if (!s) return "—";
  return s.replace(/\s*ET\s*$/, "").trim();
}

function isPlaceholderTime(s: string): boolean {
  if (!s || s === "—") return false;
  return !s.includes(":");
}

function shortBook(name: string): string {
  if (!name) return "";
  const n = name.trim().toLowerCase();
  if (n.startsWith("draftking"))   return "DK";
  if (n.startsWith("fanduel"))     return "FD";
  if (n.startsWith("betmgm"))      return "MGM";
  if (n.startsWith("caesars"))     return "CAE";
  if (n.startsWith("pinnacle"))    return "PIN";
  return name.slice(0, 3).toUpperCase();
}

/** Render a projection and the floor it's compared against so the two never
 *  display as the same rounded value (which would read as a contradiction
 *  like "0.84 below 0.84"). 2 dp normally; 3 dp only when they'd tie. */
function fmtFloorPair(proj: number, floor: number): { proj: string; floor: string } {
  let p = proj.toFixed(2), f = floor.toFixed(2);
  if (p === f) { p = proj.toFixed(3); f = floor.toFixed(3); }
  return { proj: p, floor: f };
}

function lambdaTooltip(row: BoardRow): string {
  const lam = row.lambda.toFixed(2);
  const yrfi = row.yrfiPct.toFixed(1);
  const lr = row.lambdaLrTotal;                              // the run estimate the floor actually checks
  let note = "";
  if (lr != null) {
    const f = fmtFloorPair(lr, row.yrfiFloorUsed);           // weather-adjusted floor the predictor used
    note = lr < row.yrfiFloorUsed
      ? `\n• Model's run projection ${f.proj} is below this game's ${f.floor} YRFI floor → would-be YRFI demoted to PASS - LOW λ`
      : `\n• Model's run projection ${f.proj} is above this game's ${f.floor} YRFI floor → YRFI bets enabled when YRFI% high enough`;
  }
  return `Combined λ ${lam} (rough total-runs estimate; the betting floor uses the model projection below)\nP(YRFI) ${yrfi}%${note}`;
}

function normalizeAmericanOdds(raw: string): string {
  const s = (raw || "").trim();
  if (!s) return "";
  return /^[+\-]/.test(s) ? s : (Number.parseFloat(s) > 0 ? `+${s}` : s);
}

/** Convert American odds to implied probability (excludes vig). */
export function parseAmericanToImpliedProb(s: string | null | undefined): number | null {
  if (!s) return null;
  const trimmed = s.trim().replace("−", "-");
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n === 0) return null;
  if (n > 0) return 100 / (n + 100);
  return -n / (-n + 100);
}

function relAge(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const ageSec = Math.max(0, (Date.now() - t) / 1000);
  if (ageSec < 60)    return `${Math.round(ageSec)}s ago`;
  if (ageSec < 3600)  return `${Math.round(ageSec / 60)} min ago`;
  if (ageSec < 86400) return `${Math.round(ageSec / 3600)} h ago`;
  return `${Math.round(ageSec / 86400)} d ago`;
}

/** True for STRONG NRFI / STRONG YRFI rows -- these get the elevated
 *  visual treatment (leading stripe, faint card tint, heavier border).
 *  PASS / LEAN / pending rows recede. */
function isActionableRow(side: PickSide, strength: PickStrength): boolean {
  return strength === "STRONG" && (side === "NRFI" || side === "YRFI");
}

/** True for rows that should visually recede so actionable ones stand
 *  out -- PASS picks, lineup-pending without a clear tentative lean,
 *  starter-pending.  When we DO have a clear tentative lean, the row
 *  is informative (the user can see which way the model is leaning
 *  even before lineups post) so it shouldn't recede. */
function isRecedeRow(
  side:        PickSide,
  strength:    PickStrength,
  hasTentativeLean: boolean,
): boolean {
  if (strength === "STRONG" && (side === "NRFI" || side === "YRFI")) return false;
  // Tentative lean visible -> not recede (the row is conveying signal).
  if (strength === "LINEUP PENDING" && hasTentativeLean) return false;
  return (
    side === "PASS" ||
    strength === "LINEUP PENDING" ||
    strength === "STARTER PENDING" ||
    strength === "NO EDGE" ||
    strength === "NO DATA" ||
    strength === "LOW LAMBDA"
  );
}

/** Effective tone class for the row.  Normally just toneClass(side,
 *  strength).  When the row is LINEUP PENDING with a clear tentative
 *  lean, swap to the lean's side tone (nrfiLean / yrfiLean) so the
 *  right-side gradient + pill tint signal which way the model is
 *  leaning, even though the pick isn't committed yet.  Pill keeps its
 *  dashed border via .pickPillPending so it still reads as tentative.
 */
function effectiveToneClass(
  side: PickSide,
  strength: PickStrength,
  tentative: { side: PickSide; strength: PickStrength } | null,
): string {
  if (strength === "LINEUP PENDING" && tentative) {
    if (tentative.side === "NRFI") return "nrfiLean";
    if (tentative.side === "YRFI") return "yrfiLean";
  }
  return toneClass(side, strength);
}

export function BoardRowItem({
  row,
  detail,
  live,
  expanded,
  onToggle,
  thresholds,
  slateDate,
  replayStakes,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
  live?: LiveGameState;
  expanded: boolean;
  onToggle: () => void;
  thresholds?: PickThresholds;
  slateDate?: string;
  /** Quarter-Kelly stakes the current model would place on this slate,
   *  keyed by replayKey(). Absent for a slate the replay has not
   *  covered yet (tonight). */
  replayStakes?: Map<string, ReplayStake>;
}) {

  // T3.22: compute tentative lean once at the row level.  Only meaningful
  // when LINEUP PENDING (pitcher real, hitters fallback).  Threaded down
  // to PickPill (renders inline lean) and used here for tone + recede
  // logic so the row visually signals which way the model is leaning.
  const tentativeLean = shouldShowTentative(row.pickStrength)
    ? classifyTentative(
        row.nrfiPct / 100,
        detail?.lambdaLrTotal ?? row.lambda,
        thresholds,
      )
    : null;
  const hasClearTentativeLean =
    tentativeLean !== null && tentativeLean.side !== "PASS";

  const tone = effectiveToneClass(
    row.pickSide,
    row.pickStrength,
    hasClearTentativeLean ? tentativeLean : null,
  );
  const actionable = isActionableRow(row.pickSide, row.pickStrength);
  const recede     = isRecedeRow(row.pickSide, row.pickStrength, hasClearTentativeLean);

  // T1.3: pulse the row when meaningful data changes (grade landing,
  // pick flipping, P&L update).  Watching a tuple keyed on the
  // user-visible deltas so noisy fields don't trigger.
  const pulseKey = `${detail?.gradedResult ?? ""}|${row.pickLabel}|${detail?.profitLossUnits ?? ""}|${detail?.fiTotalRuns ?? ""}`;
  const justChanged = useRecentlyChanged(pulseKey, 2500);

  const rowClasses = [
    styles.row,
    styles[tone],
    expanded   ? styles.open            : "",
    actionable ? styles.actionable      : "",
    recede     ? styles.recede          : "",
    justChanged ? styles.rowJustChanged : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={rowClasses}>
      <button
        type="button"
        className={styles.clickable}
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={`details-${row.away}-${row.home}`}
      >
        <span className={styles.spine} aria-hidden />

        <TimeCell value={formatGameTime(row.gameTimeEt || detail?.gameTimeEt || "")} />

        <span className={styles.matchup}>
          <span className={styles.team}>{row.away}</span>
          <span className={styles.at}>at</span>
          <span className={styles.team}>{row.home}</span>
          {row.doubleHeader && row.doubleHeader !== "N" && (
            <span className={styles.dhTag} title={`Doubleheader Game ${row.gameNumber}`}>
              DH-{row.gameNumber}
            </span>
          )}
          <DataQualityBadge detail={detail} />
        </span>

        <span className={styles.resultCell}>
          {detail?.gradedResult ? (
            <ResultBadge
              graded={detail.gradedResult}
              actual={detail.actualSide}
              awayRuns={detail.fiAwayRuns}
              homeRuns={detail.fiHomeRuns}
              totalRuns={detail.fiTotalRuns}
            />
          ) : live && live.abstractGameState !== "Preview" ? (
            <LiveStateBadge live={live} />
          ) : (
            // T3.22: pre-game state used to render as a static "—".  Now
            // we surface time-to-first-pitch ("47m", "1h 23m", "PRE") so
            // the operator can see when each game starts at a glance.
            <PreGameBadge
              gameTimeEt={row.gameTimeEt || detail?.gameTimeEt || ""}
              slateDate={slateDate ?? ""}
              live={live}
            />
          )}
        </span>

        <span className={styles.pickCell}>
          <PickPill
            row={row}
            detail={detail}
            slateDate={slateDate ?? ""}
            tentativeLean={tentativeLean}
          />
          <PassReasonChip row={row} />
          <EliteHitterChip detail={detail} />
          <OddsChip row={row} detail={detail} />
          <StakeChip row={row} detail={detail} thresholds={thresholds} replay={replayStakes} />
        </span>

        <DistBar row={row} />

        <span
          className={styles.expander}
          aria-hidden
          title={expanded ? "Hide details" : "Show details"}
        >
          <span className={`${styles.caret} ${expanded ? styles.caretOpen : ""}`}>
            ⌄
          </span>
        </span>
      </button>

      {expanded && (
        <div id={`details-${row.away}-${row.home}`} className={styles.detailsWrap}>
          <GameDetails
            row={row}
            detail={detail}
            thresholds={thresholds}
            slateDate={slateDate}
          />
        </div>
      )}
    </div>
  );
}

/** Pick pill + tooltip + pre-lock countdown.  Extracted from the inline
 *  IIFE in the prior version so the JSX above stays readable.
 *
 *  T3.22: when LINEUP PENDING and the model has a clear tentative lean,
 *  render a structured label "PENDING · STRONG NRFI" with the side text
 *  tone-tinted (green for NRFI, warm-red for YRFI) so the operator can
 *  scan the slate and see which way every pending row is leaning before
 *  the lineups post.  The pill keeps its dashed border and pending dot
 *  so it still reads as tentative -- not a committed bet. */
function PickPill({
  row,
  detail,
  slateDate,
  tentativeLean,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
  slateDate: string;
  tentativeLean: { side: PickSide; strength: PickStrength } | null;
}) {
  // Once a row has a graded result the game has happened: the "still
  // waiting" visual cues (dashed border, pulsing dot, "PENDING ·"
  // prefix, pre-lock countdown) all become misleading.  Concrete case
  // -- 2026-05-08 NYY@MIL + DET@KC: lineups never posted before the
  // T-60 lock so both rows froze at PASS / LINEUP PENDING with a
  // tentative LEAN NRFI; both first innings then ended 0-0 (NRFI),
  // but the pills kept reading "PENDING · LEAN NRFI" hours after
  // the games had effectively won the lean.  Treat any graded row
  // as a settled row for display purposes.
  const isGraded = !!(detail?.gradedResult || "").trim();

  const lockAt = computeLockAt(row.gameTimeEt, slateDate);
  const isPreLock =
    !isGraded
    && lockAt !== null
    && Date.now() < lockAt.getTime()
    && (detail?.betPlaced || "") !== "Y"
    && (row.pickStrength === "STRONG" || row.pickStrength === "LEAN");
  const lockTimeStr = lockAt ? formatLockTime(lockAt) : "";

  const pendingClass =
    !isGraded
    && (row.pickStrength === "STARTER PENDING"
        || row.pickStrength === "LINEUP PENDING"
        || isPreLock)
      ? styles.pickPillPending
      : "";

  // 2026-07-29.  THE ONE ROW ON THE BOARD WITH A DEADLINE.
  //
  // A STRONG pick that has not locked yet is the only thing on this page
  // that expires: the operator has until `lockTimeStr` to get the bet
  // down, and the tracker's T-60 window means that is between 30 and 60
  // minutes from when it first appears.  It was styled identically to
  // "LINEUP PENDING" -- which needs nothing from anybody and resolves
  // itself -- so the single actionable state on the slate looked exactly
  // like the single most ignorable one.
  //
  // --attn is the correct token by the page's own colour law: "money AT
  // RISK / a decision is waiting on you."  This is the clearest case of
  // that on the whole surface, and it was the one place not using it.
  const isLocking = isPreLock && row.pickStrength === "STRONG";
  const lockingClass = isLocking ? styles.pickPillLocking : "";
  const lowLambdaClass = row.pickStrength === "LOW LAMBDA" ? styles.pickPillLowLambda : "";

  // Tentative lean is only rendered for LINEUP PENDING with a clear
  // NRFI/YRFI lean.  STARTER PENDING is too noisy (pitcher fallback);
  // a PASS tentative is no signal so we still show the plain pending pill.
  const showTentative =
    row.pickStrength === "LINEUP PENDING"
    && tentativeLean !== null
    && tentativeLean.side !== "PASS";
  const leanSideClass = showTentative
    ? (tentativeLean!.side === "NRFI" ? styles.pickLabelLeanNrfi : styles.pickLabelLeanYrfi)
    : "";

  // Cluster-demoted PASS rows: pick_label encodes the original verdict as
  // "PASS - Cluster demotion: STRONG YRFI (thin_pitcher_strong_v1)" so we
  // can show the operator both the model's original verdict AND why the
  // bet was skipped, without needing a separate column or detail field.
  // See tools/apply_cluster_demotion.py::DEMOTION_LABEL_PREFIX.
  const clusterDemotionMatch = row.pickLabel.startsWith("PASS - Cluster demotion")
    ? row.pickLabel.match(/^PASS - Cluster demotion:\s+(STRONG|LEAN)\s+(NRFI|YRFI)\s+\(([^)]+)\)$/)
    : null;

  const lowLamFloor = fmtFloorPair(row.lambdaLrTotal ?? row.lambda, row.yrfiFloorUsed);
  const titleText =
    showTentative
      ? (isGraded
          ? `Model's lean was: ${tentativeLean!.strength} ${tentativeLean!.side} (computed from team-fallback batter stats while waiting on lineups). No bet was placed -- the lineup never posted in time before lock.`
          : `Model's tentative lean: ${tentativeLean!.strength} ${tentativeLean!.side} based on team-fallback batter stats. Will commit (or override) once MLB posts the actual lineup -- expand the row for more context.`)
    : isPreLock
      ? `Model's current lean: ${row.pickStrength} ${row.pickSide}. Pick locks at ${lockTimeStr} (60 min pre-game). Until then, the verdict can still flip with fresh lineup / weather / scratch data, so DON'T bet yet.`
    : clusterDemotionMatch
      ? `Skipped by cluster demotion '${clusterDemotionMatch[3]}'. Model's original verdict was ${clusterDemotionMatch[1]} ${clusterDemotionMatch[2]}, but bets matching this cluster have been losing -- so no money was committed. Counted as PASS in your official P&L. Reversible: edit data/cluster_demotions.json. Run 'python tools/cluster_shadow_pnl.py' to see how skipped bets would have done.`
    : row.pickStrength === "LOW LAMBDA"
      ? `Demoted from STRONG/LEAN YRFI: the model's first-inning run projection (${lowLamFloor.proj}) is below this game's ${lowLamFloor.floor} YRFI floor (a 0.84 base, nudged by weather), so it expects too few total runs to bet YRFI confidently. Tested in backtest: floor adds ~+1.36u/season.`
    : row.pickStrength === "NO DATA"
      ? noDataReason(detail)
      : undefined;

  return (
    <span
      className={`${styles.pickPill} ${pendingClass} ${lockingClass} ${lowLambdaClass}`}
      title={titleText}
    >
      <span className={styles.pickDot} aria-hidden />
      <span className={styles.pickLabel}>
        {showTentative ? (
          <>
            {!isGraded && (
              <>
                <span className={styles.pickLabelMuted}>PENDING</span>
                <span className={styles.pickLabelSep}>·</span>
              </>
            )}
            <span className={`${styles.pickLabelLean} ${leanSideClass}`}>
              {tentativeLean!.strength} {tentativeLean!.side}
            </span>
          </>
        ) : (
          pickLabelText(row.pickSide, row.pickStrength, isPreLock, lockTimeStr)
        )}
      </span>
    </span>
  );
}

/** Short human-readable reason for a PASS row, returned as
 *  {text, tooltip} when worth showing.  Null when the pill itself
 *  already conveys the reason (LINEUP PENDING / STARTER PENDING /
 *  LOW LAMBDA all spell themselves out in the pill text).
 *
 *  Goal: glance-able "why was this PASS?" without needing to expand
 *  the row or read the tooltip.  Stays under ~12 chars so it fits
 *  inline with the pill on a single line. */
function passReasonText(
  row: BoardRow,
): { text: string; tooltip: string } | null {
  if (row.pickSide !== "PASS") return null;

  // Cluster-demoted rows: pull short label from a known-id map so the
  // chip says "thin pitcher" instead of the full cluster id.  Default
  // to "demoted" for ids we haven't named yet.
  if (row.pickLabel.startsWith("PASS - Cluster demotion")) {
    const m = row.pickLabel.match(/\(([^)]+)\)\s*$/);
    const id = m ? m[1] : "";
    const shortById: Record<string, string> = {
      "thin_pitcher_strong_v1": "thin pitcher",
      "yrfi_deep":              "deep YRFI",
    };
    const text = shortById[id] || "demoted";
    const verdictMatch = row.pickLabel.match(/STRONG (NRFI|YRFI)/);
    const verdict = verdictMatch ? `${verdictMatch[1]} (${verdictMatch[0]})` : "";
    return {
      text,
      tooltip: id
        ? `Cluster demotion '${id}'${verdict ? ` -- model originally said ${verdictMatch![0]}` : ""}. Hover the PASS pill for the full explanation.`
        : "Cluster-demoted by an active rule in data/cluster_demotions.json.",
    };
  }

  // These already spell themselves out in the pill text -- no chip.
  if (row.pickStrength === "LINEUP PENDING")  return null;
  if (row.pickStrength === "STARTER PENDING") return null;
  if (row.pickStrength === "NO DATA")         return null;   // NO DATA pill is distinct enough already

  // LOW LAMBDA: pill text already says "PASS · LOW λ" but doesn't show
  // the actual value.  Surface it: operator can see "λ 0.72" and judge
  // how close to this game's floor we are (a 0.83 demotion feels different
  // than a 0.55 demotion, even though both demote). The floor is weather-
  // adjusted per game, so show the actual value the predictor used.
  if (row.pickStrength === "LOW LAMBDA") {
    const floorN = row.yrfiFloorUsed;
    const f = fmtFloorPair(row.lambdaLrTotal ?? row.lambda, floorN);
    const wx = floorN > 0.839 ? " (raised because it's a hot/windy park today)"
             : floorN < 0.837 ? " (lowered for cold weather)"
             : "";
    return {
      text: `λ ${row.lambda.toFixed(2)}`,
      tooltip:
        `Would-be STRONG YRFI demoted to PASS.  The model's own first-inning ` +
        `run projection (${f.proj}) is below this game's ${f.floor} YRFI floor${wx}, ` +
        `so it expects too few runs to hold an edge.  (The λ ${row.lambda.toFixed(2)} on the ` +
        `row is a separate rough estimate -- the floor checks the model's ${f.proj}.)`,
    };
  }

  // The "true PASS" case: calibrated P(NRFI) landed in the PASS zone
  // (between PASS_LO 44% and STRONG_NRFI 56%).  Model isn't confident
  // either way -- no bet warranted.
  if (row.pickStrength === "NO EDGE") {
    return {
      text: "no edge",
      tooltip:
        `Calibrated P(NRFI) ${row.nrfiPct.toFixed(0)}% sits between the PASS thresholds ` +
        `(44% PASS_LO / 56% STRONG_NRFI) -- model has no confident lean either way.`,
    };
  }
  return null;
}

function PassReasonChip({ row }: { row: BoardRow }) {
  const reason = passReasonText(row);
  if (!reason) return null;
  return (
    <span className={styles.passReason} title={reason.tooltip}>
      {reason.text}
    </span>
  );
}

/** Mean top-3 ISO for a posted lineup.  Null when the lineup is empty
 *  (LINEUP PENDING -- no per-batter data yet to look at).
 *  ISO = SLG - BA, measures pure power output (extra bases per AB). */
function meanTop3Iso(lineup: BatterLine[] | undefined): number | null {
  if (!lineup || lineup.length === 0) return null;
  const isos = lineup.slice(0, 3)
    .map((b) => b.iso)
    .filter((v): v is number => v != null && Number.isFinite(v));
  if (isos.length === 0) return null;
  return isos.reduce((a, b) => a + b, 0) / isos.length;
}

/** Single-batter peak ISO from the top 3 -- catches the Aaron-Judge
 *  case where one elite hitter pulls the team mean up.  Null when
 *  lineup empty. */
function peakTop3Iso(lineup: BatterLine[] | undefined): { iso: number; name: string } | null {
  if (!lineup || lineup.length === 0) return null;
  let best: { iso: number; name: string } | null = null;
  for (const b of lineup.slice(0, 3)) {
    if (b.iso == null || !Number.isFinite(b.iso)) continue;
    if (best == null || b.iso > best.iso) {
      best = { iso: b.iso, name: b.name || "?" };
    }
  }
  return best;
}

/** "Elite power" heads-up chip.  Renders when at least one side has
 *  meaningful top-3 power (mean ISO >= 0.220) OR a single top-3
 *  batter has ISO >= 0.300.  Tooltip names which side + which player.
 *
 *  Why this exists: the LR model has both top3c_iso and top3c_slg
 *  as features, but they're high-magnitude opposite-sign coefficients
 *  that can mostly cancel.  When elite power is on the field the
 *  signal still leaks through, but the operator might still want a
 *  visible "watch out for the Yankees lineup" reminder regardless of
 *  what the model said. */
function EliteHitterChip({ detail }: { detail: GameDetail | undefined }) {
  if (!detail) return null;
  const awayMean = meanTop3Iso(detail.away?.lineup);
  const homeMean = meanTop3Iso(detail.home?.lineup);
  const awayPeak = peakTop3Iso(detail.away?.lineup);
  const homePeak = peakTop3Iso(detail.home?.lineup);

  // Thresholds: 0.220 team-mean = solid power; 0.300 individual = elite.
  // (League-avg ISO ~ 0.169; 0.220 ≈ 75th percentile lineups,
  //  0.300 ≈ top ~20 hitters.)
  const MEAN_FLOOR = 0.220;
  const INDIVIDUAL_FLOOR = 0.300;

  type Side = { teamLabel: string; mean: number | null; peak: { iso: number; name: string } | null };
  const sides: Side[] = [
    { teamLabel: detail.away?.team || "away", mean: awayMean, peak: awayPeak },
    { teamLabel: detail.home?.team || "home", mean: homeMean, peak: homePeak },
  ];
  const flagged = sides.filter(
    (s) => (s.mean != null && s.mean >= MEAN_FLOOR)
        || (s.peak != null && s.peak.iso >= INDIVIDUAL_FLOOR),
  );
  if (flagged.length === 0) return null;

  // Build the tooltip: list each flagged side's mean + the peak hitter.
  const parts: string[] = [];
  for (const s of flagged) {
    const segs: string[] = [];
    if (s.mean != null && s.mean >= MEAN_FLOOR) {
      segs.push(`top-3 ISO ${s.mean.toFixed(3)}`);
    }
    if (s.peak != null && s.peak.iso >= INDIVIDUAL_FLOOR) {
      segs.push(`${s.peak.name} ISO ${s.peak.iso.toFixed(3)}`);
    }
    parts.push(`${s.teamLabel}: ${segs.join(", ")}`);
  }
  const tooltip =
    `Elite top-3 power detected (heads-up, not a pick override):\n• `
    + parts.join("\n• ")
    + `\n\nLeague-average top-3 ISO ~0.169.  Watch out for first-inning home runs.`;

  return (
    <span
      className={`${styles.passReason} ${styles.eliteHitter}`}
      title={tooltip}
    >
      elite power
    </span>
  );
}

/** Data-quality badge -- shown when ANY model input is using a fallback
 *  rather than real data. */
function DataQualityBadge({ detail }: { detail: GameDetail | undefined }) {
  if (!detail) return null;
  const issues: string[] = [];
  const ap = detail.away.pitcher.quality;
  const hp = detail.home.pitcher.quality;
  const ao = detail.away.offense.quality;
  const ho = detail.home.offense.quality;
  if (ap === "avg") issues.push(`${detail.away.team} pitcher: TBD / league-avg fallback`);
  if (hp === "avg") issues.push(`${detail.home.team} pitcher: TBD / league-avg fallback`);
  if (ao === "avg") issues.push(`${detail.away.team} offense: league-avg fallback`);
  if (ho === "avg") issues.push(`${detail.home.team} offense: league-avg fallback`);
  if (detail.away.lineup.length === 0) {
    issues.push(`${detail.away.team} lineup: not posted yet`);
  }
  if (detail.home.lineup.length === 0) {
    issues.push(`${detail.home.team} lineup: not posted yet`);
  }
  if (issues.length === 0) return null;
  const severity = ap === "avg" || hp === "avg" ? "high" : "med";
  return (
    <span
      className={`${styles.dqBadge} ${severity === "high" ? styles.dqHigh : styles.dqMed}`}
      title={`Data quality issues:\n• ${issues.join("\n• ")}`}
      aria-label={`Data quality: ${issues.length} issues`}
    >
      <span aria-hidden>!</span>
    </span>
  );
}


/** Kelly stake chip (2026-07-28).
 *
 *  WHY THIS EXISTS.  The board was built when every bet was flat 1u, so
 *  stake was implicit and never shown.  Quarter Kelly went live
 *  2026-07-27 and now produces stakes from roughly 3.9u to 10u on the
 *  same slate -- so the one screen that tells the operator WHAT to bet
 *  was not telling them HOW MUCH.  That is a functional gap, not a
 *  cosmetic one.
 *
 *  Mirrors tracker.kelly_stake_units: quarter Kelly on the model's
 *  probability at the real captured price, capped per bet.  It does NOT
 *  apply the same-day exposure cap, because that depends on what the
 *  rest of the slate commits and is allocated first-come-first-served at
 *  bet time -- so this is the stake BEFORE any daily trim.  Labelled
 *  "up to" for exactly that reason rather than implying a firm number.
 *
 *  Renders only for STRONG picks with a real captured price. No price
 *  means no Kelly stake is computable, and inventing one is the same
 *  mistake as the -110 fallback that inflated April. */
function StakeChip({
  row, detail, thresholds, replay,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
  thresholds: PickThresholds | undefined;
  replay: Map<string, ReplayStake> | undefined;
}) {
  // WHAT *YOU* STAKED WINS. ALWAYS. (2026-07-30)
  //
  // This used to try the REPLAY first and return unconditionally, so a
  // game the operator really bet showed the replay's figure under the
  // word "staked" -- a claim about the past that was simply false.
  // 2026-07-29 HOU@LAA read "staked 17.00u" on a bet placed at 5.97u,
  // and TOR@WSH read 6.00u against a real 2.08u.
  //
  // The gap is not a rounding artefact, it is two different bankrolls:
  // the replay compounds from 100u and had reached ~257u by that date,
  // while the real bank is ~88u. Same quarter-Kelly fraction, ~3x the
  // absolute units. A board that says "staked 17u" is telling the
  // operator to risk three times what the system actually sizes.
  //
  // Order now: the ledger's recorded stake, then the replay as a
  // clearly-labelled suggestion. Pre-Kelly rows read "1.00u" under this
  // rule, which was the old objection to ledger-first -- but 1.00u is
  // what was bet on those nights, so it is the true answer.
  // THE NEW SYSTEM'S STAKE (2026-07-30). Computed from the model
  // probability and the price, because 1 unit = 1% of bankroll makes
  // sizing bankroll-free -- so this chip shows the SAME number every
  // other surface shows, and the same number a subscriber would bet.
  //
  // It deliberately no longer prints detail.unitsRisked. That is what
  // the ledger RECORDED, and anything placed before today was sized the
  // old way (bankroll x Kelly%), which is why 2026-07-29 read 5.97u here
  // and 7u on /history for one bet.
  const stakePriceRaw = (row.pickSide === "NRFI"
    ? detail?.marketNrfiOdds : detail?.marketYrfiOdds) || "";
  const stakeAmerican = Number.parseFloat(stakePriceRaw.trim());
  const stakeModelP = (row.pickSide === "NRFI" ? row.nrfiPct : row.yrfiPct) / 100;
  if (Number.isFinite(stakeAmerican) && stakeAmerican !== 0) {
    const u = stakeUnitsFor(stakeModelP, stakeAmerican);
    if (u > 0) {
      return (
        <span className={styles.stakeChip}>
          <span className={styles.stakeLabel}>stake</span>
          <span className={styles.stakeValue}>{u.toFixed(2)}u</span>
        </span>
      );
    }
  }

  // No real bet on this row. The replay's figure may still be worth
  // showing -- the current gate stakes games the old one passed on --
  // but it is labelled "system", never "staked", because nobody staked
  // it. Lookup tries the row's own side first, then either side, since
  // the labels can differ between the two gates.
  const rp = replay && (
    replay.get(replayKey(row.away, row.home, row.gameNumber, row.pickSide)) ??
    replay.get(replayKey(row.away, row.home, row.gameNumber, "YRFI")) ??
    replay.get(replayKey(row.away, row.home, row.gameNumber, "NRFI"))
  );
  if (rp?.action === "BET" && typeof rp.stake === "number") {
    return (
      <span className={styles.stakeChip} data-sim="1">
        <span className={styles.stakeLabel}>system</span>
        <span className={styles.stakeValue}>
          {rp.stake.toFixed(2)}u{rp.assumed ? "*" : ""}
        </span>
      </span>
    );
  }

  if (row.pickStrength !== "STRONG") return null;
  if (row.pickSide !== "NRFI" && row.pickSide !== "YRFI") return null;
  const t = thresholds;
  if (!t || t.kellyEnabled !== true || typeof t.kellyFraction !== "number") return null;
  if (!detail) return null;

  // 2026-07-28: the whole dashboard reads in quarter-Kelly now. For any
  // date the replay has covered, show what the CURRENT model would stake
  // -- NOT detail.unitsRisked, which is a flat 1.00 on every row placed
  // before Kelly went live and made April read "staked 1.00u" throughout.
  // These are the same figures the day-reconcile table prints.
  // Replayed, and the current model declined it.
  if (rp?.action === "SKIP") {
    return (
      <span className={`${styles.stakeChip} ${styles.stakeChipZero}`}>
        <span className={styles.stakeLabel}>model</span>
        <span className={styles.stakeValue}>passes</span>
      </span>
    );
  }

  // Not replayed yet (tonight). A locked row's recorded stake IS the live
  // Kelly figure, so it is authoritative here.
  if (detail.betPlaced === "Y" && detail.unitsRisked != null) {
    return (
      <span className={styles.stakeChip}>
        <span className={styles.stakeLabel}>staked</span>
        <span className={styles.stakeValue}>{detail.unitsRisked.toFixed(2)}u</span>
      </span>
    );
  }

  const raw = row.pickSide === "NRFI" ? detail.marketNrfiOdds : detail.marketYrfiOdds;
  const american = Number.parseFloat(normalizeAmericanOdds(raw));
  if (!Number.isFinite(american) || american === 0) return null;

  const p = (row.pickSide === "NRFI" ? row.nrfiPct : row.yrfiPct) / 100;
  if (!(p > 0 && p < 1)) return null;

  const b = american > 0 ? american / 100 : 100 / Math.abs(american);
  const full = (p * b - (1 - p)) / b;
  if (!(full > 0)) {
    // Kelly says this bet has no positive expectation at this price.
    return (
      <span className={`${styles.stakeChip} ${styles.stakeChipZero}`}>
        <span className={styles.stakeLabel}>stake</span>
        <span className={styles.stakeValue}>no edge</span>
      </span>
    );
  }
  // Audit 2026-07-28: size from the COMPOUNDED bank the tracker actually
  // uses, not the static nominal 100u -- in a drawdown the static figure
  // OVERSTATES the stake the operator is about to place.
  const bank = t.kellyCurrentBankrollUnits ?? t.kellyBankrollUnits ?? 100;
  const frac = Math.min(full * t.kellyFraction, t.kellyMaxStakeFrac ?? 0.1);
  const units = bank * frac;
  if (units < (t.kellyMinStakeUnits ?? 0.1)) return null;

  return (
    <span className={styles.stakeChip}>
      <span className={styles.stakeLabel}>stake up to</span>
      <span className={styles.stakeValue}>{units.toFixed(1)}u</span>
    </span>
  );
}

/** Live-odds chip: sportsbook + price.  Tone tinted by row.pickSide.
 *  PASS rows render BOTH sides ("DK  N -130 · Y +100"); active picks
 *  render just the picked side ("DK  N -135").
 *
 *  T-redesign: drift arrow + drift label dropped from the row-inline
 *  rendering -- they live in GameDetails LineDriftNotice now.  The
 *  row stays cleaner; the operator can drill into the details panel
 *  to see whether the market moved toward or away from the pick. */
function OddsChip({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  if (!detail) return null;

  const nrfiPrice = normalizeAmericanOdds(detail.marketNrfiOdds);
  const yrfiPrice = normalizeAmericanOdds(detail.marketYrfiOdds);

  // T-V21-2026-05-06: when a STRONG bet was placed but DK odds were
  // never captured (typical "DK closed market before our scraper got
  // there" pattern), still surface the row's bet status with a
  // -110 fallback chip.  Without this the row showed nothing at all
  // and the user couldn't tell from the chip area whether a bet had
  // been placed.  This branch only renders for placed-but-no-odds
  // STRONG bets; PASS rows + no-data pre-game rows still hide.
  if (!nrfiPrice && !yrfiPrice) {
    const isPlacedStrong =
      detail.betPlaced === "Y" &&
      (row.pickSide === "NRFI" || row.pickSide === "YRFI");
    if (!isPlacedStrong) return null;
    const sideLabel = row.pickSide === "NRFI" ? "N" : "Y";
    const toneClass =
      row.pickSide === "NRFI" ? styles.oddsNrfi : styles.oddsYrfi;
    return (
      <span
        className={`${styles.oddsChip} ${toneClass}`}
        title={
          `Bet placed: ${row.pickSide} (no DK price captured at lock).` +
          `\nP&L computed at the standard -110 payout fallback.` +
          `\nThis happens when DK closes the market before the scraper` +
          ` reaches the game.`
        }
      >
        <span className={styles.oddsBook}>DK</span>
        <span className={styles.oddsPair}>
          <span className={styles.oddsSideLabel}>{sideLabel}</span>
          <span className={styles.oddsPrice}>-110*</span>
        </span>
      </span>
    );
  }

  const book = shortBook(detail.sportsbook);
  const ageStr = relAge(detail.oddsCapturedAt);
  const ageSuffix = ageStr ? `\nCaptured ${ageStr}` : "";
  const bet = detail.betPlaced;

  const toneClass =
    row.pickSide === "NRFI" ? styles.oddsNrfi
    : row.pickSide === "YRFI" ? styles.oddsYrfi
    : styles.oddsPending;

  const skipClass =
    bet === "N" && row.pickSide !== "PASS" ? styles.oddsSkipped : "";

  // PASS branch: both sides as informational market data.
  if (row.pickSide === "PASS") {
    return (
      <span
        className={`${styles.oddsChip} ${toneClass}`}
        title={
          `Market on ${detail.sportsbook || "DK"}: `
          + (nrfiPrice ? `NRFI ${nrfiPrice}` : "NRFI —")
          + " · "
          + (yrfiPrice ? `YRFI ${yrfiPrice}` : "YRFI —")
          + "\nModel declined or pending; no bet."
          + ageSuffix
        }
      >
        {book && <span className={styles.oddsBook}>{book}</span>}
        {nrfiPrice && (
          <span className={styles.oddsPair}>
            <span className={styles.oddsSideLabel}>N</span>
            <span className={styles.oddsPrice}>{nrfiPrice}</span>
          </span>
        )}
        {nrfiPrice && yrfiPrice && <span className={styles.oddsSep}>·</span>}
        {yrfiPrice && (
          <span className={styles.oddsPair}>
            <span className={styles.oddsSideLabel}>Y</span>
            <span className={styles.oddsPrice}>{yrfiPrice}</span>
          </span>
        )}
      </span>
    );
  }

  // NRFI / YRFI active pick -- single side, tone-colored, with edge tail.
  const price = row.pickSide === "NRFI" ? nrfiPrice : yrfiPrice;
  if (!price) return null;
  const sideLabel = row.pickSide === "NRFI" ? "N" : "Y";
  const edgePct = detail.edgeOnPick != null ? detail.edgeOnPick * 100 : null;
  const edgeStr = edgePct == null
    ? ""
    : (edgePct >= 0 ? `+${edgePct.toFixed(1)}%` : `${edgePct.toFixed(1)}%`);

  // 2026-07-28: this line used to render whenever `detail.clvPct != null`,
  // and so it claimed a measured "CLV: +0.00pp" on essentially every priced
  // chip.  Two separate defects produced that:
  //   - board-supabase.ts (the PRODUCTION read path) coerced a NULL clv_pct
  //     to 0, so the `!= null` guard never fired outside local dev;
  //   - the ledger genuinely stores 0.0000 for most placed bets, because we
  //     bet the first price we see and lock it (T2.23), so the opened and
  //     current prices are the same captured number.  Of 314 placed bets
  //     since 2026-05-07 that have both prices, 295 are identical.
  // Measurability therefore comes from the PRICES, not from clv_pct: only
  // claim a CLV when the picked side has both an opened and a current price
  // and they differ -- the same guard LineDriftNotice already uses.  The
  // rounding check keeps a real-but-negligible move from printing "+0.00pp",
  // which reads as a small win rather than as nothing.
  const openedRaw = (row.pickSide === "NRFI"
    ? detail.openedNrfiOdds
    : detail.openedYrfiOdds).trim();
  const currentRaw = (row.pickSide === "NRFI"
    ? detail.marketNrfiOdds
    : detail.marketYrfiOdds).trim();
  const lineMoved = Boolean(openedRaw && currentRaw && openedRaw !== currentRaw);
  const clvPp =
    lineMoved && typeof detail.clvPct === "number" && Number.isFinite(detail.clvPct)
      ? detail.clvPct * 100
      : null;
  const clvSuffix =
    clvPp != null && Math.abs(clvPp) >= 0.005
      ? `\nCLV: ${clvPp > 0 ? "+" : "−"}${Math.abs(clvPp).toFixed(2)}pp`
      : "";

  const titleText = (bet === "Y"
    ? `Bet placed: ${row.pickSide} @ ${price} (edge ${edgeStr})`
    : bet === "N"
      ? `Skipped: edge ${edgeStr || "below threshold"} on ${row.pickSide} @ ${price}`
      : `${row.pickSide} @ ${price}${edgeStr ? ` (edge ${edgeStr})` : ""}`)
    + clvSuffix
    + ageSuffix;

  return (
    <span
      className={`${styles.oddsChip} ${toneClass} ${skipClass}`}
      title={titleText}
    >
      {book && <span className={styles.oddsBook}>{book}</span>}
      <span className={styles.oddsPair}>
        <span className={styles.oddsSideLabel}>{sideLabel}</span>
        <span className={styles.oddsPrice}>{price}</span>
      </span>
      {edgeStr && (
        <span className={styles.oddsEdge} aria-label={`edge ${edgeStr}`}>
          {edgeStr}
        </span>
      )}
    </span>
  );
}


/** Live state badge -- shown in the result column for in-progress games.
 *  Shows current inning + score for live games, "PRE" / "WARMUP" / "DELAY"
 *  for pre-game states, "FINAL" for ungraded finals (rare). */
function LiveStateBadge({ live }: { live: LiveGameState }) {
  const state = live.abstractGameState;
  const status = live.status;

  if (state === "Preview") {
    if (status === "Warmup") {
      return <span className={`${styles.liveBadge} ${styles.liveWarmup}`}>WARMUP</span>;
    }
    return <span className={`${styles.liveBadge} ${styles.livePre}`}>PRE-GAME</span>;
  }

  if (status.toLowerCase().includes("delay")) {
    return <span className={`${styles.liveBadge} ${styles.liveDelay}`}>DELAY</span>;
  }

  if (state === "Live") {
    const inn  = live.currentInning ?? 0;
    const half = (live.inningState || "").toLowerCase();
    const halfCode =
      half.startsWith("top")    ? "T" :
      half.startsWith("bot")    ? "B" :
      half.startsWith("mid")    ? "M" :
      half.startsWith("end")    ? "E" : "·";
    const score = `${live.awayScore}-${live.homeScore}`;
    const isFirstInning = inn === 1;

    return (
      <span
        className={`${styles.liveBadge} ${styles.liveLive} ${isFirstInning ? styles.live1stPulse : ""}`}
        title={
          `${live.away} @ ${live.home}\n` +
          `${state} · ${live.inningState || "—"} of ${inn}\n` +
          `Score: ${live.awayScore}-${live.homeScore}\n` +
          (live.firstInningComplete && live.firstInningTotalRuns != null
            ? `1st inning: ${live.firstInningAwayRuns}-${live.firstInningHomeRuns} (${live.firstInningTotalRuns} total runs) — ${live.firstInningTotalRuns === 0 ? "NRFI" : "YRFI"}`
            : "1st inning still in progress")
        }
      >
        <span className={styles.liveInning}>{halfCode}{inn}</span>
        <span className={styles.liveScore}>{score}</span>
      </span>
    );
  }

  if (state === "Final") {
    if (live.firstInningTotalRuns != null) {
      const r = live.firstInningTotalRuns;
      return (
        <span
          className={`${styles.liveBadge} ${styles.liveFinal}`}
          title={`Final · 1st: ${live.firstInningAwayRuns}-${live.firstInningHomeRuns}`}
        >
          {`F · 1st ${r}r`}
        </span>
      );
    }
    return <span className={`${styles.liveBadge} ${styles.liveFinal}`}>FINAL</span>;
  }

  return <span className={styles.resultPending}>—</span>;
}


/** PreGameBadge -- replaces the prior "—" em-dash placeholder for rows
 *  that have no live state yet (most of the slate, most of the day).
 *
 *  Shows time-to-first-pitch as a compact mono countdown:
 *    "47m"        for sub-hour
 *    "1h 23m"     for hour-or-more
 *    "PRE"        if we couldn't parse the game time
 *    em-dash      if the game time has already passed but we still
 *                 have no live data (odd state -- preserves prior
 *                 behavior so we don't over-claim freshness)
 *
 *  Visual: same livePre dashed muted treatment used by LiveStateBadge,
 *  so the State column reads as a single visual family across pre-game
 *  / live / final states.  Re-evaluates Date.now() on every render --
 *  the dashboard's 30s polling refresh updates the countdown for free.
 */
function PreGameBadge({
  gameTimeEt,
  slateDate,
  live,
}: {
  gameTimeEt: string;
  slateDate:  string;
  live?:      LiveGameState;
}) {
  // If we have live data with abstractGameState=Preview, defer to
  // LiveStateBadge so the WARMUP / DELAY / PRE-GAME variants render
  // consistently when the API is reporting them.
  if (live && live.abstractGameState === "Preview") {
    return <LiveStateBadge live={live} />;
  }

  if (!gameTimeEt || !slateDate) {
    return <span className={`${styles.liveBadge} ${styles.livePre}`}>PRE</span>;
  }
  const gameStart = computeLockAt(gameTimeEt, slateDate, 0);
  if (!gameStart) {
    return (
      <span
        className={`${styles.liveBadge} ${styles.livePre}`}
        title={`First pitch ${gameTimeEt}`}
      >
        PRE
      </span>
    );
  }

  const ms = gameStart.getTime() - Date.now();
  if (ms <= 0) {
    // Game start has passed but we have no live state -- could mean the
    // useLiveGameState hook hasn't picked it up yet, or the game is
    // historical.  Fall back to the prior em-dash so we don't claim
    // "PRE" on a game that's actually in progress somewhere.
    return <span className={styles.resultPending}>—</span>;
  }

  const totalMin = Math.floor(ms / 60_000);
  const hours = Math.floor(totalMin / 60);
  const mins = totalMin % 60;
  const text =
    totalMin >= 1440
      ? `${Math.floor(totalMin / 1440)}d`
      : hours >= 1
        ? (mins > 0 ? `${hours}h ${mins}m` : `${hours}h`)
        : `${Math.max(1, mins)}m`;

  return (
    <span
      className={`${styles.liveBadge} ${styles.livePre}`}
      title={`First pitch at ${gameTimeEt}`}
    >
      {text}
    </span>
  );
}


/** Time column renderer.  Real wall-clock times use the mono .rank
 *  treatment.  Non-numeric placeholders ("After Game 1", "TBD") render
 *  as a small dashed chip so the column rhythm isn't broken. */
function TimeCell({ value }: { value: string }) {
  if (isPlaceholderTime(value)) {
    const display = value.replace(/\bGame\s+(\d+)\b/i, "G$1");
    return (
      <span
        className={styles.rankTag}
        title={`${value} — start time pending until prior game finishes`}
      >
        <span className={styles.rankTagDot} aria-hidden />
        {display}
      </span>
    );
  }
  return <span className={`num ${styles.rank}`}>{value}</span>;
}


/** DistBar -- segmented NRFI<->YRFI probability bar with end-cap labels.
 *  Replaces the prior 3-cell lineup of (LambdaMeter | NRFI% | YRFI%) by
 *  combining all three into a single visualization:
 *
 *    [NRFI %]  [###green###|###red###]  [YRFI %]
 *
 *  Two complementary fills: green from the left at width=NRFI%, red
 *  from the right at width=YRFI%.  They meet at the actual probability
 *  split.  A faint center tick marks the 50/50 reference; an indicator
 *  pin marks the precise probability point on actionable rows.
 *
 *  Tooltip aggregates the underlying lambda + zone reasoning that the
 *  separate LambdaMeter / pct cells used to surface individually. */
function DistBar({ row }: { row: BoardRow }) {
  const nrfi = Math.max(0, Math.min(100, row.nrfiPct));
  const yrfi = Math.max(0, Math.min(100, row.yrfiPct));
  const yrfiPos = Math.max(0, Math.min(100, yrfi));

  // ONE numeral, not two (2026-07-29).  The pair always summed to 100:
  // "29.8  [bar]  70.2" spends two of the row's scarcest slots saying one
  // thing, and doubles the figures the operator's eye has to sort through
  // on a 16-row board.  The BAR already shows the split; the numeral only
  // has to say how far.
  //
  // WHICH side it reports is the pick side when there is one, else
  // whichever side the bar favours -- so the number is never the one the
  // reader has to subtract from 100 to get the one they wanted.  The tag
  // makes that explicit rather than leaving it to be inferred from which
  // end the digits sit at.
  const side: "NRFI" | "YRFI" =
    row.pickSide === "NRFI" || row.pickSide === "YRFI"
      ? row.pickSide
      : nrfi >= yrfi ? "NRFI" : "YRFI";
  const shown = side === "NRFI" ? nrfi : yrfi;

  return (
    <span
      className={styles.distBar}
      role="img"
      aria-label={`Probability split: NRFI ${nrfi.toFixed(1)}%, YRFI ${yrfi.toFixed(1)}%`}
      title={lambdaTooltip(row)}
    >
      <span className={styles.distTrack}>
        <span
          className={styles.distNrfiFill}
          style={{ width: `${nrfi}%` }}
          aria-hidden
        />
        <span
          className={styles.distYrfiFill}
          style={{ width: `${yrfi}%` }}
          aria-hidden
        />
        <span className={styles.distMidMark} aria-hidden />
        <span
          className={styles.distPin}
          style={{ left: `${yrfiPos}%` }}
          aria-hidden
        />
      </span>
      <span className={styles.distLabel}>
        <span className={styles.distLabelSide} data-side={side}>{side}</span>
        <span className="num">{shown.toFixed(1)}</span>
      </span>
    </span>
  );
}


function ResultBadge({
  graded,
  actual,
  awayRuns,
  homeRuns,
  totalRuns,
}: {
  graded:    NonNullable<GameDetail["gradedResult"]>;
  actual:    GameDetail["actualSide"];
  awayRuns:  number | null;
  homeRuns:  number | null;
  totalRuns: number | null;
}) {
  const score =
    awayRuns != null && homeRuns != null
      ? `${awayRuns}-${homeRuns}`
      : totalRuns != null
        ? `${totalRuns}R`
        : "-";
  const totalText = totalRuns != null ? `${totalRuns}R` : "-";

  const ariaScore = awayRuns != null && homeRuns != null
    ? `${awayRuns} runs away, ${homeRuns} runs home`
    : totalRuns != null
      ? `${totalRuns} total runs`
      : "score not yet recorded";

  if (graded === "POSTPONED" || graded === "SUSPENDED") {
    return (
      <span
        className={`${styles.resultBadge} ${styles.resultPP}`}
        title={graded}
        aria-label={`Game ${graded.toLowerCase()}, no result.`}
        role="status"
      >
        <span className={styles.resultGlyph}>PP</span>
      </span>
    );
  }
  if (graded === "PASS") {
    return (
      <span
        className={`${styles.resultBadge} ${styles.resultPass}`}
        title={`PASS · 1st inning ${score} · ${actual ?? "-"} (${totalText})`}
        aria-label={`Pass: model declined. First inning final ${ariaScore}, actual side ${actual ?? "unknown"}.`}
        role="status"
      >
        <span className={styles.resultGlyph}>{score}</span>
      </span>
    );
  }
  const isWin = graded === "WIN";
  const cls   = isWin ? styles.resultWin : styles.resultLoss;
  const glyph = isWin ? "W" : "L";
  const outcomeWord = isWin ? "Win" : "Loss";
  return (
    <span
      className={`${styles.resultBadge} ${cls}`}
      title={`${graded} · 1st inning ${score} · ${actual ?? "-"} (${totalText})`}
      aria-label={`${outcomeWord}. First inning ${ariaScore}, actual side ${actual ?? "unknown"}.`}
      role="status"
    >
      <span className={styles.resultGlyph}>{glyph}</span>
      <span className={styles.resultRuns}>{score}</span>
    </span>
  );
}
