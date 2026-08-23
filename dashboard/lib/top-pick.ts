/**
 * THE #1 PICK, UNDER THE RULES IN FORCE TODAY.
 *
 * MEASURED AT THE PUBLISHED STAKING RULE. Every game, price and result
 * is real ledger data. The STAKE comes from quarter-Kelly, which is what
 * the system tells a follower to bet -- so this is the system's record,
 * not a simulation of it, in exactly the way a flat-1u figure is also
 * not a simulation. Operator, 2026-08-03: *"stop saying SIMULATED...
 * if we were actually betting using quarter kelly everywhere, we would
 * be in profit."* They are right, and the earlier label was overcautious.
 *
 * `totals.realized` is still carried and still printed, because the
 * operator staked a flat unit before 2026-07-27 and the gap is large
 * (+7.49u against +68.23u on the #1 series). That is a fact about
 * EXECUTION worth being able to see. It is not a correction to the
 * figure beside it, and the UI no longer frames it as one.
 *
 * NOTHING COMPOUNDS, ANYWHERE. Operator: *"compounding is up to the
 * bettor, not the system."* Correct, and it is the same principle that
 * makes a published stake bankroll-free -- the system emits a unit
 * COUNT, and what a unit is worth after a winning week is the
 * follower's business. Every bank level, peak and drawdown is gone.
 *
 * WHAT "#1" MEANS HERE. The top YRFI play of each night by confidence,
 * then by the better price. NRFI is excluded outright -- it was
 * switched off 2026-06-07 for losing in every band -- so on a night
 * whose overall #1 was an NRFI play, the best YRFI play is #1 instead.
 * The series starts at CURRENT_SYSTEM_FROM, when the live weights were
 * fit; earlier picks came from a model that no longer exists.
 *
 * ONE POPULATION, THROUGHOUT. Every figure below — record, hit rate,
 * break-even, units staked, units returned — is computed over the SAME
 * set: placed, graded, and carrying a real captured price. An earlier
 * draft counted the record over every STRONG pick (115 nights) while
 * the money covered only the ones actually bet (88), which is the
 * "two figures, two populations, one table" defect this dashboard has
 * been cleared of repeatedly. Rows without a captured price are
 * excluded and COUNTED, so the exclusion is visible rather than silent.
 *
 * WHY THE MONEY IS A RATIO AND NEVER A TOTAL. 1 unit = 1% of bankroll,
 * so units from different dates are amounts in different currencies and
 * summing them is not a quantity (see lib/units.ts). `roiPerUnit` is
 * returned ÷ staked — scale-free, comparable across any window, and the
 * figure a follower on any bankroll would have experienced.
 */
import { loadLedgerRows } from "./roi";
import { compareForTopPick } from "./top-pick-rank";
import { stakeUnitsFor } from "./kelly-sim";

export interface TopPickBet {
  date: string;
  game: string;
  side: "YRFI" | "NRFI";
  /** p(no run in the 1st) as printed at pick time. */
  modelP: number;
  odds: number;
  win: boolean;
  /** What the ledger RECORDED. Mostly 1.00u: Kelly went live 07-27. */
  unitsRisked: number;
  /** REALIZED P&L at that recorded stake. */
  pnl: number;
  /** Did this row reach WIN or LOSS? Carried so the settlement check can
   *  happen AFTER the night's #1 is ranked -- filtering unsettled rows out
   *  before ranking is what let a postponed top play promote the
   *  runner-up into the published record (2026-06-11). */
  settled: boolean;
  /** Stake under the published quarter-Kelly rule. What the system says
   *  to bet, and what a follower on any bankroll would stake. */
  kellyStake: number;
  /** P&L at that stake, at the real captured price and the real result. */
  kellyPnl: number;
}

export interface TopPickWindow {
  label: string;
  /** Days covered by the window, whether or not they had a bet. */
  spanDays: number;
  from: string;
  to: string;
  bets: number;
  wins: number;
  losses: number;
  /** wins / bets */
  hitRate: number;
  /** Average break-even rate the prices actually paid demand. */
  breakEven: number;
  /** Real units staked and real units returned, same bets. */
  staked: number;
  returned: number;
  /** returned / staked — THE money figure. A ratio, never a total. */
  roiPerUnit: number;
  /** The same bets at a flat 1u each: the edge before staking. */
  flatRoi: number;
  /** Wilson 95% interval on the hit rate. */
  ciLo: number;
  ciHi: number;
  /** Nights whose #1 pick had no captured DK price, so was excluded. */
  excludedNoPrice: number;
}

/**
 * One cut of the series: a month.
 *
 * Every figure is a straight sum at quarter-Kelly. Nothing compounds:
 * the system publishes a unit COUNT and what a unit is worth after a
 * winning week is the bettor's business, not the system's. That also
 * makes these rows directly comparable with each other, which a
 * compounded figure never is.
 */
export interface TopPickSlice {
  key: string;
  bets: number;
  wins: number;
  losses: number;
  hitRate: number;
  breakEven: number;
  staked: number;
  /** Units returned at quarter-Kelly. A straight sum, nothing compounds. */
  returned: number;
  /** returned ÷ staked, the per-unit view of the same thing. */
  roiPerUnit: number;
}

export interface TopPickReport {
  windows: TopPickWindow[];
  /** Most recent first — the run of results behind the headline. */
  recent: TopPickBet[];
  generatedFor: string;
  /** The last ten settled #1 plays, most recent first. */
  last10: { wins: number; losses: number; bets: TopPickBet[] };
  /**
   * THREE TOTALS, ALL EXACT SUMS, DISTINGUISHED BY BASIS.
   *
   *   atKelly   the published staking rule -- THE system figure
   *   atFlat1u  the same picks at one unit each: the edge, size removed
   *   realized  what the operator's own ledger recorded
   *
   * `atKelly` is not a simulation and is no longer labelled as one.
   * Every game, price and result in it is real; only the STAKE comes
   * from the rule rather than from history, which is exactly as true of
   * `atFlat1u`. Operator, 2026-08-03: quarter-Kelly is what the system
   * says to bet, so it is what the system's record should be measured
   * at. `realized` stays alongside because the operator was staking a
   * flat unit before 2026-07-27 and that difference is a fact worth
   * being able to see, not a correction to the figure beside it.
   *
   * Nothing here compounds. All three are sums on a fixed unit value,
   * so they mean the same on a $1,000 bank and a $10,000 one.
   */
  totals: {
    /** Flat 1u a night: the edge with stake size removed. FACT. */
    atFlat1u: number;
    /** What was ACTUALLY staked and returned. The only realized figure. */
    realized: number;
    /** The published quarter-Kelly rule. THE system figure. */
    atKelly: number;
  };
  /** Nights where today's Kelly rule finds no edge at the price paid, so
   *  the current system would not bet at all. Disclosed, not hidden. */
  noEdgeUnderKelly: number;
  /** Calendar months, oldest first. */
  byMonth: TopPickSlice[];
  /** Every settled #1 play, most recent first. */
  all: TopPickBet[];
  /**
   * Cumulative units at quarter-Kelly, one point per settled night.
   *
   * A RUNNING SUM, NOT A BANK. It replaced the equity curve on
   * 2026-08-03, and the difference is the whole point: an equity curve
   * compounds, so its later moves are drawn bigger than its earlier ones
   * for the same unit result. This does not, so a +5u night in May and a
   * +5u night in August are the same height of line.
   */
  cumulative: { date: string; units: number }[];
  /**
   * The same rule applied to nights on/after MODEL_UPDATED_FROM only: the
   * updated model's own record, beside -- never blended into -- the season
   * figure. `bets` is 0 until its first No.1 settles.
   */
  sinceUpdate: {
    from: string;
    bets: number;
    wins: number;
    losses: number;
    atKelly: number;
    atFlat1u: number;
    staked: number;
  };
}

/** The live model weights were fit on this date. Everything before it
 *  was picked by a model that no longer exists, and April's prices were
 *  barely captured, so the series starts here. */
export const CURRENT_SYSTEM_FROM = "2026-05-26";

/** 2026-08-23: the live weights were updated (pooled first-inning pitcher
 *  xwOBA + L2 0.5, 20 features; ship commit f7952566, pushed 10:18 ET on
 *  Aug 23 -- the Aug 22 slate was still picked by the previous weights, which
 *  is why this is the 23rd even though the work is logged under Aug 22).
 *  THE SERIES DOES NOT RESET -- the operator's rule is one continuous ledger,
 *  the new model's picks append -- but the nights from this date are ALSO
 *  summed on their own so the updated model can be read next to the season
 *  figure without blending into it. */
export const MODEL_UPDATED_FROM = "2026-08-23";

const num = (v: string | undefined): number | null => {
  if (v == null) return null;
  const s = v.trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

/** Profit per 1u staked on a win. */
function payout(odds: number): number {
  return odds > 0 ? odds / 100 : 100 / -odds;
}

/** Break-even win rate the price demands, vig included. */
function implied(odds: number): number {
  return odds < 0 ? -odds / (-odds + 100) : 100 / (odds + 100);
}

/** Wilson interval — honest at the sample sizes a single-pick-per-night
 *  series produces. A 6-bet window lands ~60 points wide, which is the
 *  point: it tells the reader the window cannot answer anything. */
function wilson(k: number, n: number, z = 1.96): [number, number] {
  if (n === 0) return [0, 1];
  const ph = k / n;
  const d = 1 + (z * z) / n;
  const c = ph + (z * z) / (2 * n);
  const m = z * Math.sqrt((ph * (1 - ph)) / n + (z * z) / (4 * n * n));
  return [(c - m) / d, (c + m) / d];
}

function isoMinus(iso: string, days: number): string {
  const t = Date.parse(`${iso}T00:00:00Z`);
  return new Date(t - days * 86_400_000).toISOString().slice(0, 10);
}

/* The ranking rule lives in lib/top-pick-rank.ts -- pure arithmetic,
   no imports -- because BoardTable is a CLIENT component and importing
   it from here would drag this file's `node:fs` dependency into the
   browser bundle. Re-exported so existing importers keep working. */
export type { RankableBet } from "./top-pick-rank";
export { compareForTopPick } from "./top-pick-rank";

/** The night's #1, with the game name settling a full tie so the result
 *  is fully determined even when confidence AND price both match. */
function better(a: TopPickBet, b: TopPickBet): TopPickBet {
  const c = compareForTopPick(a, b);
  if (c !== 0) return c < 0 ? a : b;
  return a.game <= b.game ? a : b;
}

const WINDOWS: { label: string; days: number | null }[] = [
  { label: "Season to date", days: null },
  { label: "Last 30 days", days: 30 },
  { label: "Last 14 days", days: 14 },
  { label: "Last 7 days", days: 7 },
];

export async function loadTopPickReport(
  season: number,
  /** `false` keeps EVERY qualifying bet instead of one a night, which is
   *  the whole system rather than its top play. Same rules, same
   *  staking, same shape, so the two sections can be read against each
   *  other without the reader having to hold two definitions. */
  topOnly = true,
): Promise<TopPickReport | null> {
  const rows = await loadLedgerRows(season);
  if (!rows) return null;

  /* ============================================================
     THIS SECTION IS THE CURRENT SYSTEM, NOT THE OLD LEDGER.
     ============================================================
     Rewritten 2026-08-03 on the operator's instruction, and the three
     changes compound, so read them together:

       1. YRFI ONLY. STRONG NRFI was switched off 2026-06-07 for losing
          in every band. 15 of the 92 nights in the old series had an
          NRFI play as their #1, and showing them as the record of a
          system that would not place them is simply wrong.
       2. FROM 2026-05-26, the date the live model weights were fit.
          Earlier picks came from a model that no longer exists, and
          April's prices were barely captured.
       3. STAKED AT QUARTER-KELLY. 85 of the old 92 bets were recorded
          at exactly 1.00u because Kelly only went live 2026-07-27. The
          section now sizes every night by the rule in force TODAY, via
          the same `stakeUnitsFor` the board uses -- bankroll-free, 10u
          per-bet cap, so it is the number a follower would stake.

     WHAT THAT MAKES THESE FIGURES. All three points are FILTERS or the
     STAKING RULE, and none of them re-scores a game: every matchup,
     price and result stays real ledger data. `atKelly` is therefore the
     SYSTEM'S RECORD, not a simulation of it -- in exactly the way
     `atFlat1u` is also not a simulation. Operator, 2026-08-03: *"stop
     saying SIMULATED... if we were actually betting using quarter kelly
     everywhere, we would be in profit."*

     `realized` is carried alongside because the operator staked a flat
     unit until 2026-07-27 and the gap is large (~7x: the median stake
     goes from 1.00u to 5.00u). That is a fact about EXECUTION worth
     being able to see. It is NOT a correction to the figure beside it,
     the UI does not frame it as one, and neither should any prose
     describing this file -- see the header comment, which is the
     authority here.

     DO NOT re-label the Kelly figures "simulated" / "counterfactual" /
     "backtest". This block used to do exactly that, contradicting the
     header, and on 2026-08-04 it caused a session to answer "what is my
     real profit on the #1 picks" with the realized number instead of
     the Kelly one. The operator had to overrule the same point twice in
     two days. One file, one story.

     KELLY ALSO DROPS BETS, which is easy to miss: `stakeUnitsFor`
     returns 0 when the model has no edge at the price actually paid,
     so 2 of the 65 qualifying nights are not bets at all under today's
     rule. Counted and disclosed rather than silently sized to zero.
     ============================================================ */
  const all: TopPickBet[] = [];
  const unpricedNights = new Set<string>();
  const unsettledTopNights = new Set<string>();
  let noEdgeUnderKelly = 0;
  // RANK FIRST, THEN REQUIRE A RESULT. `graded` is deliberately NOT a
  // filter here any more -- it is checked after the night's #1 is chosen.
  // Filtering unsettled rows out up front silently promoted the
  // second-best game on a night whose top play was postponed, and counted
  // ITS result as the #1's. Live example, 2026-06-11: the top YRFI play
  // ATL@CWS (p 0.3219) was POSTPONED and the record counted CHC@COL
  // (0.3543), which LOST. A postponement is NO ACTION, not a licence to
  // substitute a different game. Mirrors tools/pl_calc.select_top_picks.
  for (const r of rows) {
    const graded = (r.graded_result || "").trim().toUpperCase();
    if ((r.bet_placed || "").trim().toUpperCase() !== "Y") continue;
    const side = (r.pick_side || "").trim();
    if (side !== "YRFI") continue;                       // (1) NRFI is off
    const date = (r.date || "").trim();
    if (date < CURRENT_SYSTEM_FROM) continue;            // (2) current weights
    const modelP = num(r.nrfi_prob);
    if (modelP == null) continue;
    const odds = num(r.market_yrfi_odds);
    const unitsRisked = num(r.units_risked);
    const pnl = num(r.profit_loss_units);
    // RANKING NEEDS ONLY THE PRICE. `unitsRisked` and `pnl` are empty on
    // an unsettled row, so requiring them here would filter the night's
    // real #1 out before it could be ranked -- which is exactly how a
    // postponed top play promoted the runner-up. They are folded into
    // `settled` below and checked AFTER ranking.
    if (odds == null || odds === 0) {
      unpricedNights.add(date);
      continue;
    }
    all.push({
      date,
      game: `${(r.away_team || "").trim()}@${(r.home_team || "").trim()}`,
      side, modelP, odds, win: graded === "WIN",
      unitsRisked: unitsRisked ?? 0, pnl: pnl ?? 0,
      settled: (graded === "WIN" || graded === "LOSS")
               && unitsRisked != null && pnl != null,
      // Placeholders; the real values are set once the night's #1 is known.
      kellyStake: 0, kellyPnl: 0,
    });
  }
  if (all.length === 0) return null;

  // One bet per night: the strongest. With NRFI filtered out above this
  // is already the "re-rank to the top YRFI play" rule -- on a night
  // whose overall #1 was NRFI, the best YRFI play becomes #1.
  const byDay = new Map<string, TopPickBet[]>();
  for (const b of all) {
    const list = byDay.get(b.date);
    if (list) list.push(b);
    else byDay.set(b.date, [b]);
  }
  const tops: TopPickBet[] = [];
  for (const d of [...byDay.keys()].sort()) {
    const night = byDay.get(d)!;
    const chosen = topOnly ? [night.reduce(better)] : night;
    for (const b of chosen) {
      // THE NIGHT'S #1 MUST HAVE SETTLED. Checked here, after ranking, so
      // an unsettled top play excludes the night instead of promoting the
      // runner-up into the record. In `topOnly` mode this is the whole
      // point; in full mode it just drops the unsettled rows as before.
      if (!b.settled) { unsettledTopNights.add(b.date); continue; }
      // (3) size it the way the system sizes a bet today. p is the
      // probability of the SIDE BET, and modelP is p(no run), so YRFI
      // takes the complement -- the same conversion StakeChip makes.
      const stake = stakeUnitsFor(1 - b.modelP, b.odds);
      if (stake <= 0) { noEdgeUnderKelly++; continue; }
      b.kellyStake = stake;
      b.kellyPnl = b.win ? stake * payout(b.odds) : -stake;
      tops.push(b);
    }
  }
  if (tops.length === 0) return null;

  const end = tops[tops.length - 1].date;
  const windows: TopPickWindow[] = [];
  for (const w of WINDOWS) {
    const start = w.days == null ? "" : isoMinus(end, w.days - 1);
    const sel = tops.filter((b) => b.date >= start);
    if (sel.length === 0) continue;
    const n = sel.length;
    const wins = sel.filter((b) => b.win).length;
    const staked = sel.reduce((a, b) => a + b.kellyStake, 0);
    const returned = sel.reduce((a, b) => a + b.kellyPnl, 0);
    const flat = sel.reduce((a, b) => a + (b.win ? payout(b.odds) : -1), 0);
    const be = sel.reduce((a, b) => a + implied(b.odds), 0) / n;
    const [lo, hi] = wilson(wins, n);
    const excluded = [...unpricedNights].filter(
      (d) => d >= start && d <= end && !byDay.has(d),
    ).length;
    windows.push({
      label: w.label,
      spanDays: w.days ?? 0,
      from: sel[0].date, to: sel[n - 1].date,
      bets: n, wins, losses: n - wins,
      hitRate: wins / n,
      breakEven: be,
      staked, returned,
      roiPerUnit: staked > 0 ? returned / staked : 0,
      flatRoi: flat / n,
      ciLo: lo, ciHi: hi,
      excludedNoPrice: excluded,
    });
  }

  /* ---------------- the compounding view ----------------
     A unit is 1% of bankroll, so a bet returning +5.44u grew the bank by
     5.44% -- `bank *= 1 + pnl/100`, the same rule the daily ledger uses.
     Returning LEVELS (start, end, peak) rather than a sum is the only
     way a multi-date money figure is a quantity at all. */
  const slice = (sel: TopPickBet[], key: string): TopPickSlice => {
    const n = sel.length;
    const wins = sel.filter((b) => b.win).length;
    const staked = sel.reduce((a, b) => a + b.kellyStake, 0);
    const returned = sel.reduce((a, b) => a + b.kellyPnl, 0);
    return {
      key,
      bets: n,
      wins,
      losses: n - wins,
      hitRate: n > 0 ? wins / n : 0,
      breakEven: n > 0 ? sel.reduce((a, b) => a + implied(b.odds), 0) / n : 0,
      staked,
      returned,
      roiPerUnit: staked > 0 ? returned / staked : 0,
    };
  };

  const months = new Map<string, TopPickBet[]>();
  for (const b of tops) {
    const m = b.date.slice(0, 7);
    const l = months.get(m);
    if (l) l.push(b);
    else months.set(m, [b]);
  }

  const last10 = tops.slice(-10).reverse();

  return {
    windows,
    recent: tops.slice(-12).reverse(),
    generatedFor: end,
    last10: {
      wins: last10.filter((b) => b.win).length,
      losses: last10.filter((b) => !b.win).length,
      bets: last10,
    },
    totals: {
      atFlat1u: tops.reduce((a, b) => a + (b.win ? payout(b.odds) : -1), 0),
      realized: tops.reduce((a, b) => a + b.pnl, 0),
      atKelly: tops.reduce((a, b) => a + b.kellyPnl, 0),
    },
    noEdgeUnderKelly,
    byMonth: [...months.keys()].sort().map((m) => slice(months.get(m)!, m)),
    all: [...tops].reverse(),
    sinceUpdate: (() => {
      const sel = tops.filter((b) => b.date >= MODEL_UPDATED_FROM);
      const wins = sel.filter((b) => b.win).length;
      return {
        from: MODEL_UPDATED_FROM,
        bets: sel.length,
        wins,
        losses: sel.length - wins,
        atKelly: sel.reduce((a, b) => a + b.kellyPnl, 0),
        atFlat1u: sel.reduce((a, b) => a + (b.win ? payout(b.odds) : -1), 0),
        staked: sel.reduce((a, b) => a + b.kellyStake, 0),
      };
    })(),
    cumulative: (() => {
      const byDate = new Map<string, number>();
      for (const b of tops) {
        byDate.set(b.date, (byDate.get(b.date) ?? 0) + b.kellyPnl);
      }
      let run = 0;
      return [...byDate.keys()].sort().map((date) => {
        run += byDate.get(date)!;
        return { date, units: run };
      });
    })(),
  };
}
