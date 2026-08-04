/**
 * THE ONE COPY OF THE CLASSIFIER, and nothing else.
 *
 * WHY THIS IS ITS OWN FILE. `classifyTentative` lived in
 * `components/BoardRow.tsx`, which carries "use client". That was fine
 * while only client components needed it. On 2026-08-04 the SERVER-side
 * brief page needed it too, to answer "does this lineup-pending row have
 * a real lean behind it, or is it a genuine pass?" -- and importing it
 * from a client module to answer that would drag a client boundary into
 * a server render for what is ten lines of arithmetic.
 *
 * This is the same move `lib/top-pick-rank.ts` records, for the same
 * reason and with the same payoff: the rule is pure, so both sides can
 * share it, and the board and the brief cannot drift apart about what
 * the model is leaning toward. Two surfaces answering that question with
 * their own private copy is how this dashboard has produced
 * contradictions before.
 *
 * NO IMPORTS BUT TYPES. Keep it that way -- the moment this file pulls
 * in `node:fs` or a React component, the server/client sharing that
 * justifies its existence stops working.
 */
import type { PickSide, PickStrength, PickThresholds } from "./types";

/** Default classifier thresholds -- a fallback for the first render
 *  before `data/thresholds.json` loads, and for a deploy whose cached
 *  board response predates the field. These MATCH the values the
 *  predictor writes on every run; when the predictor's gates move,
 *  these move with them.
 *
 *  Phase 1.3 (2026-05-12): LEAN tier reactivated as track-only with
 *  leanNrfiP = 0.50 / leanYrfiP = 0.50. */
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
 *  PENDING guards).
 *
 *  Phase 1.3 (2026-05-12): restructured to fire LEAN YRFI for
 *  passLoP < pNrfi < leanYrfiP when lambda clears the floor. The
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
    // PASS "HIGH LAMBDA". Only fires inside the STRONG-NRFI branch, so
    // it cannot affect any YRFI verdict. Older deploys omit the field;
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

/**
 * WHAT A ROW WOULD BE BRIEFED AS, or null when it earns no brief.
 *
 * THE RULE, AND WHY IT MOVED (2026-08-04). It first read "STRONG or LEAN
 * only", which was right about committed picks and wrong about the board
 * in practice: on the 08-04 slate 14 of 15 games were LINEUP PENDING, so
 * the only brief button anywhere was the #1's, and the operator
 * reasonably reported it as "every brief takes me to the #1".
 *
 * A LINEUP PENDING row is a PASS in the ledger, but it is not the model
 * declining to have an opinion -- it is the model waiting. The board
 * already prints its tentative lean. And critically, EVERY FIGURE THE
 * BRIEF SHOWS IS ALREADY FINAL: the park rate, both teams' last-10
 * first innings, both starters' scoreless-first records and the
 * head-to-head all come from team codes, pitcher ids and the ledger.
 * None of them reads the lineup. So the case is fully writable; only the
 * VERDICT is provisional, and the page says so and prints no stake.
 *
 * STARTER PENDING STAYS OUT, and that is the line. There the pitcher
 * data is league-average fallback on BOTH sides, so the two biggest
 * figures on the page would be fabrications. A pending row whose
 * tentative is itself a PASS stays out too -- that one really is the
 * model having no opinion.
 */
export interface BriefVerdict {
  side: "NRFI" | "YRFI";
  /** The tentative tier for a pending row; the real one otherwise. */
  strength: "STRONG" | "LEAN";
  /** The pick has not committed yet -- lineups are still out. */
  pending: boolean;
}

export function briefVerdictOf(
  row: {
    pickSide: PickSide;
    pickStrength: PickStrength;
    nrfiPct: number;
    lambda: number;
    lambdaLrTotal: number | null;
  },
  thresholds: PickThresholds | undefined,
  /** The model's own run projection when the detail row carries one. */
  lambdaOverride?: number | null,
): BriefVerdict | null {
  const side = row.pickSide;
  if (side === "NRFI" || side === "YRFI") {
    if (row.pickStrength === "STRONG" || row.pickStrength === "LEAN") {
      return { side, strength: row.pickStrength, pending: false };
    }
    return null;
  }

  if (row.pickStrength !== "LINEUP PENDING") return null;
  const lambda = lambdaOverride ?? row.lambdaLrTotal ?? row.lambda;
  const t = classifyTentative(row.nrfiPct / 100, lambda, thresholds);
  if (t.side !== "NRFI" && t.side !== "YRFI") return null;
  return {
    side: t.side,
    strength: t.strength === "STRONG" ? "STRONG" : "LEAN",
    pending: true,
  };
}
