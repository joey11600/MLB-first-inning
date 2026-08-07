/**
 * Counterfactual Kelly bankroll — "what would the record look like if we
 * had been staking by Kelly from the start?"
 *
 * WHY THIS IS A SEPARATE LAYER AND NOT A LEDGER REWRITE
 * -----------------------------------------------------
 * The obvious way to make the dashboard show a Kelly record is to
 * overwrite `units_risked` / `profit_loss_units` in picks_<year>.csv.
 * That must not happen. Those columns record what was ACTUALLY risked at
 * a real price; overwriting them with a simulation destroys the only
 * copy of the real position and makes the simulation permanently
 * unauditable — precisely the 2026-05-05 backfill-mirror failure that
 * CLAUDE.md's data-integrity rules exist to prevent.
 *
 * So the ledger keeps the truth (flat 1u), and this module recomputes the
 * counterfactual on read. It is regenerable, comparable against reality,
 * and impossible to corrupt.
 *
 * CONFIG COMES FROM data/thresholds.json, which the predictor writes from
 * tracker.py's own constants. Re-deriving Kelly's parameters here in
 * TypeScript would silently drift from what actually stakes the bets.
 *
 * APRIL CANNOT BE SIMULATED. 170 of April's 176 placed bets never had a
 * real DraftKings price captured — they settled against a flat -110
 * placeholder. Kelly's stake is a function of the price, so a bet whose
 * price was never observed cannot be sized without inventing one, and
 * inventing one is what made April look like +39u in the first place.
 * Those rows are skipped and counted in `skippedNoPrice` so the UI can
 * say so out loud rather than quietly starting the curve in May.
 */

export interface KellyConfig {
  enabled: boolean;
  fraction: number;
  bankrollUnits: number;
  maxStakeFrac: number;
  maxDailyFrac: number;
  minStakeUnits: number;
}

export const KELLY_FALLBACK: KellyConfig = {
  enabled: false,
  fraction: 0.25,
  bankrollUnits: 100,
  maxStakeFrac: 0.1,
  maxDailyFrac: 0.15,
  minStakeUnits: 0.1,
};

export interface KellySim {
  /** false when thresholds.json had no Kelly block (older deploy). */
  available: boolean;
  config: KellyConfig;
  startBank: number;
  finalBank: number;
  profit: number;
  /** bets Kelly actually funded */
  bets: number;
  wins: number;
  losses: number;
  /** Kelly sized these to zero — no positive expected value */
  skippedZeroEdge: number;
  /** no real captured price, so unsizeable (April, mostly) */
  skippedNoPrice: number;
  maxDrawdownPct: number;
  largestStakeUnits: number;
  /** first slate date the simulation could actually start from */
  firstDate: string | null;
  curve: { date: string; units: number }[];
  /** the same games staked flat 1u, for an apples-to-apples comparison */
  flatProfit: number;
}

function num(s: string | undefined | null): number | null {
  if (s === undefined || s === null) return null;
  const t = String(s).trim().replace("−", "-").replace("–", "-");
  if (!t) return null;
  const v = Number.parseFloat(t);
  return Number.isFinite(v) ? v : null;
}

/** Net profit per 1 unit staked. */
function payoutPerUnit(american: number): number {
  return american > 0 ? american / 100 : 100 / Math.abs(american);
}

/** Full-Kelly fraction of bankroll; 0 when the bet has no edge. */
export function kellyFraction(p: number, american: number): number {
  const b = payoutPerUnit(american);
  if (!(b > 0) || !(p > 0 && p < 1)) return 0;
  return Math.max((p * b - (1 - p)) / b, 0);
}

export function readKellyConfig(
  thresholds: Record<string, unknown> | null | undefined,
): { cfg: KellyConfig; available: boolean } {
  if (!thresholds || thresholds["kellyFraction"] === undefined) {
    return { cfg: KELLY_FALLBACK, available: false };
  }
  const pick = (k: string, d: number) => {
    const v = thresholds[k];
    return typeof v === "number" && Number.isFinite(v) ? v : d;
  };
  return {
    available: true,
    cfg: {
      enabled: thresholds["kellyEnabled"] === true,
      fraction: pick("kellyFraction", KELLY_FALLBACK.fraction),
      bankrollUnits: pick("kellyBankrollUnits", KELLY_FALLBACK.bankrollUnits),
      maxStakeFrac: pick("kellyMaxStakeFrac", KELLY_FALLBACK.maxStakeFrac),
      maxDailyFrac: pick("kellyMaxDailyFrac", KELLY_FALLBACK.maxDailyFrac),
      minStakeUnits: pick("kellyMinStakeUnits", KELLY_FALLBACK.minStakeUnits),
    },
  };
}

/**
 * Replay every graded STRONG bet in date order, compounding.
 *
 * Mirrors tracker.kelly_stake_units: quarter-Kelly by default, per-bet
 * cap, and a same-day exposure cap — the last one matters because Kelly
 * sizes one bet against one outcome, whereas a slate settles together
 * (see KELLY_MAX_DAILY_FRAC in tracker.py).
 *
 * Deliberately ALWAYS runs from the first eligible bet of the season, not
 * from the caller's window: a bankroll is a running quantity and a
 * "last 7 days" bankroll is meaningless.
 */
export function simulateKelly(
  rows: Record<string, string>[],
  cfg: KellyConfig,
  available: boolean,
): KellySim {
  const out: KellySim = {
    available,
    config: cfg,
    startBank: cfg.bankrollUnits,
    finalBank: cfg.bankrollUnits,
    profit: 0,
    bets: 0,
    wins: 0,
    losses: 0,
    skippedZeroEdge: 0,
    skippedNoPrice: 0,
    maxDrawdownPct: 0,
    largestStakeUnits: 0,
    firstDate: null,
    curve: [],
    flatProfit: 0,
  };

  // Bets that were ACTUALLY PLACED, graded, and real-money-eligible.
  //
  // bet_placed === "Y" is load-bearing, not incidental. Filtering on
  // pick_strength alone picks up 519 graded STRONG rows when only 353
  // were ever bet -- the other 166 were demoted by the cluster rules or
  // never reached the lock window. Simulating those changes the bet
  // SELECTION as well as the sizing, which answers a different question
  // and flatters the result badly (it turned a genuine -10.89u into a
  // fictional +95.51u during development). The counterfactual must hold
  // selection fixed and vary only the stake.
  type Bet = { date: string; p: number; odds: number; win: boolean };
  const byDay = new Map<string, Bet[]>();
  for (const r of rows) {
    const graded = (r.graded_result ?? "").trim().toUpperCase();
    if (graded !== "WIN" && graded !== "LOSS") continue;
    if ((r.bet_placed ?? "").trim().toUpperCase() !== "Y") continue;
    const side = (r.pick_side ?? "").trim().toUpperCase();
    if (side !== "NRFI" && side !== "YRFI") continue;

    const p = num(side === "NRFI" ? r.nrfi_prob : r.yrfi_prob);
    const odds = num(side === "NRFI" ? r.market_nrfi_odds : r.market_yrfi_odds);
    if (p === null || odds === null || odds === 0) {
      out.skippedNoPrice += 1;   // unsizeable without a real observed price
      continue;
    }
    const date = (r.date ?? "").trim();
    if (!date) continue;
    if (!byDay.has(date)) byDay.set(date, []);
    byDay.get(date)!.push({ date, p, odds, win: graded === "WIN" });
  }

  const days = Array.from(byDay.keys()).sort();
  if (days.length === 0) return out;
  out.firstDate = days[0];

  let bank = cfg.bankrollUnits;
  let peak = bank;
  for (const d of days) {
    const morning = bank;          // no intraday compounding
    let committed = 0;
    let pnl = 0;
    for (const b of byDay.get(d)!) {
      out.flatProfit += b.win ? payoutPerUnit(b.odds) : -1;

      let f = Math.min(kellyFraction(b.p, b.odds) * cfg.fraction, cfg.maxStakeFrac);
      let stake = morning * f;
      // Same-day exposure ceiling, first-come-first-served exactly as
      // tracker.py allocates it.
      const room = morning * cfg.maxDailyFrac - committed;
      stake = Math.min(stake, Math.max(room, 0));
      if (stake < cfg.minStakeUnits) {
        out.skippedZeroEdge += 1;
        continue;
      }
      stake = Math.round(stake * 100) / 100;
      committed += stake;
      out.bets += 1;
      out.largestStakeUnits = Math.max(out.largestStakeUnits, stake);
      if (b.win) {
        out.wins += 1;
        pnl += stake * payoutPerUnit(b.odds);
      } else {
        out.losses += 1;
        pnl -= stake;
      }
    }
    bank += pnl;
    peak = Math.max(peak, bank);
    if (peak > 0) {
      out.maxDrawdownPct = Math.max(out.maxDrawdownPct, ((peak - bank) / peak) * 100);
    }
    out.curve.push({ date: d, units: Math.round(bank * 100) / 100 });
    if (bank <= 0) break;
  }

  out.finalBank = Math.round(bank * 100) / 100;
  out.profit = Math.round((bank - cfg.bankrollUnits) * 100) / 100;
  out.flatProfit = Math.round(out.flatProfit * 100) / 100;
  return out;
}

/** THE SHIPPED STAKE RULE, in units. Mirrors tracker.kelly_stake_units.
 *
 *  1 UNIT = 1% OF BANKROLL (2026-07-30), so the stake IS the quarter-
 *  Kelly percentage and the bankroll never enters it. That is what lets
 *  the same number be published to every subscriber -- see
 *  tracker.kelly_stake_units for the full reasoning.
 *
 *  Exists so TONIGHT can be shown under the new system too. The nightly
 *  replay export has no entry for the live slate, so before this the
 *  dashboard's Today tab fell back to the ledger's recorded stake and
 *  disagreed with /history: 2026-07-29 read +2.04u on one page and
 *  +2.83u on the other. Because sizing is now bankroll-free, the live
 *  figure can be computed from the probability and the price alone.
 *
 *  Kept deliberately in lockstep with the Python: quarter Kelly, 10-unit
 *  per-bet cap, whole-unit rounding with a 0.5u floor, 0 on no edge.
 *  If tracker.py's constants move, move these.
 */
export const UNIT_RULE = {
  fraction: 0.25,
  maxStakeUnits: 10,     // KELLY_MAX_STAKE_FRAC 0.10 x 100
  minStakeUnits: 0.10,   // KELLY_MIN_STAKE_UNITS
  rounding: 1.0,         // KELLY_STAKE_ROUNDING
  roundedFloor: 0.5,     // KELLY_ROUNDED_FLOOR
};

/**
 * Python's `round()`, which is BANKER'S rounding (half-to-even), not
 * JavaScript's `Math.round` (half-up). They disagree at every exact .5:
 * Python round(2.5) is 2, Math.round(2.5) is 3.
 *
 * This exists so this file can reproduce `tracker.kelly_stake_units`
 * EXACTLY. See stakeUnitsFor for why "exactly" is the requirement.
 */
function roundHalfEven(value: number, digits = 0): number {
  const m = 10 ** digits;
  const v = value * m;
  const floor = Math.floor(v);
  const diff = v - floor;
  // EXACT comparison, no tolerance. A tolerance here is not a safety
  // margin, it is a bug: quarter-Kelly constantly produces values like
  // 3.4949999999999997, which Python rounds to 3.49 (and thence to 3u),
  // but which any epsilon wide enough to "absorb float dust" reads as a
  // tie and bumps to 3.50 -- and thence to 4u, a whole unit of real
  // money. Measured over 396,622 (probability, price) pairs, a 1e-9
  // tolerance produced 18 disagreements with tracker; exact comparison
  // produces none. Halves are exactly representable in binary, so the
  // tie case this needs to catch is caught exactly.
  let r: number;
  if (diff === 0.5) {
    r = floor % 2 === 0 ? floor : floor + 1;
  } else {
    r = Math.round(v);
  }
  return r / m;
}

/** Units to stake, or 0 when the model has no edge at that price. */
export function stakeUnitsFor(p: number, american: number): number {
  if (!Number.isFinite(p) || p <= 0 || p >= 1) return 0;
  const f = Math.min(
    kellyFraction(p, american) * UNIT_RULE.fraction,
    UNIT_RULE.maxStakeUnits / 100,
  );
  let stake = f * 100;
  if (stake < UNIT_RULE.minStakeUnits) return 0;   // no edge -> no bet

  // THIS FUNCTION IS A MIRROR, NOT AN INDEPENDENT IMPLEMENTATION.
  // `tracker.kelly_stake_units` is what actually SIZES THE BET -- the
  // number that gets typed into a sportsbook, recorded as
  // units_risked, and published to subscribers. Everything here only
  // DISPLAYS. So where the two disagree, the display is wrong by
  // definition, and the fix is always to match tracker.
  //
  // 2026-08-06: they disagreed by a whole unit on the night's No.1.
  // SD@ARI at p=0.6343, -135 sizes to 3.4975u exactly. tracker rounds
  // TWICE -- round(3.4975, 2) -> 3.5, then round(3.5) -> 4 -- and bet
  // 4u. This file rounded once, Math.round(3.4975) -> 3, and the board
  // printed "STAKE 3.00u" beside a bet that was placed at 4u and
  // published to Discord at 4u. Any stake landing in [x.495, x.5)
  // diverges the same way, plus every exact .5 via half-up vs
  // half-even.
  //
  // The intermediate round-to-2dp below is therefore DELIBERATE and
  // load-bearing, even though it is the very thing that makes 3.4975
  // become 4. It is not a tidy-up and removing it "because
  // double-rounding is a code smell" silently re-breaks parity.
  // Double-rounding IS arguably wrong -- but that is a question about
  // tracker's sizing rule, i.e. about real money, and it has to be
  // answered there and with the operator, never by quietly making the
  // display say something the bet did not.
  stake = roundHalfEven(stake, 2);
  if (UNIT_RULE.rounding > 0) {
    let r = roundHalfEven(stake / UNIT_RULE.rounding) * UNIT_RULE.rounding;
    if (r < UNIT_RULE.roundedFloor) r = UNIT_RULE.roundedFloor;
    // Rounding up must never breach the per-bet cap.
    if (r > UNIT_RULE.maxStakeUnits) r = stake;
    stake = r;
  }
  return roundHalfEven(stake, 2);
}

/** P&L in units for a settled bet under the same rule. */
export function pnlUnitsFor(p: number, american: number, won: boolean): number {
  const s = stakeUnitsFor(p, american);
  if (s <= 0) return 0;
  return won ? s * payoutPerUnit(american) : -s;
}
