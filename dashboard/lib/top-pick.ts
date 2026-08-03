/**
 * THE #1 PICK — how the top-ranked play of each night actually did.
 *
 * REAL MONEY, NOT THE REPLAY. Everything here comes from the ledger:
 * bets that were really placed, at prices really captured, graded
 * against real results, using the units really staked. It is NOT the
 * season-record replay that the equity curve and the week card show —
 * those re-score history with today's model, which is a different and
 * more flattering question. Both are legitimate; they must never be
 * mistaken for one another, so this file touches `season_record.json`
 * nowhere.
 *
 * WHAT "#1" MEANS. On each slate the board sorts by confidence, so the
 * #1 pick is the top row: the lowest p(no run) among YRFI plays, or the
 * highest among NRFI ones. This picks exactly that game, from the bets
 * that were actually placed.
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

export interface TopPickBet {
  date: string;
  game: string;
  side: "YRFI" | "NRFI";
  /** p(no run in the 1st) as printed at pick time. */
  modelP: number;
  odds: number;
  win: boolean;
  unitsRisked: number;
  pnl: number;
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
 * One cut of the #1-pick series: a month, a side, a park tier.
 *
 * `bankStart` / `bankEnd` are LEVELS, not a sum, which is what makes a
 * multi-bet money figure legal at all (lib/units.ts). Each slice
 * compounds from 100 so the slices are comparable with each other rather
 * than with the season run — a month that opened on a 210u bank and one
 * that opened on 100u would otherwise look wildly different for the same
 * performance.
 */
export interface TopPickSlice {
  key: string;
  bets: number;
  wins: number;
  losses: number;
  hitRate: number;
  breakEven: number;
  staked: number;
  /** Compounded from 100 within this slice. A level, never a total. */
  bankEnd: number;
  /** bankEnd/100 - 1. The scale-free figure. */
  ret: number;
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
  /** The whole series compounded from a 100u bank. Levels, not a sum. */
  bank: { start: number; end: number; ret: number; peak: number; maxDrawdown: number };
  /**
   * THE TWO HONEST SEASON TOTALS, both on a FIXED unit value.
   *
   * `atFlat1u` bets exactly 1 unit every night: the model's edge with
   * staking removed. `atPublishedStakes` bets the unit count the system
   * actually published (quarter-Kelly, 3.9u to 10u), which is what a
   * follower who never re-sizes their unit really made.
   *
   * Both add exactly, because in neither case does the unit's dollar
   * value move between bets, and both mean the same thing on a $1,000
   * bank and a $10,000 one. They are NOT the operator's own bank, which
   * compounds and is `bank` above; the three can differ in sign, which
   * is why every one of them is printed with its basis named.
   */
  totals: { atFlat1u: number; atPublishedStakes: number };
  /** Calendar months, oldest first. */
  byMonth: TopPickSlice[];
  /** YRFI and NRFI, each compounded from 100 in isolation. */
  bySide: TopPickSlice[];
  /** Every settled #1 play, most recent first. */
  all: TopPickBet[];
}

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
): Promise<TopPickReport | null> {
  const rows = await loadLedgerRows(season);
  if (!rows) return null;

  // Every placed, graded, really-priced bet. Nights whose top pick was
  // unpriced are tracked separately so the count can be disclosed.
  const all: TopPickBet[] = [];
  const unpricedNights = new Set<string>();
  for (const r of rows) {
    const graded = (r.graded_result || "").trim().toUpperCase();
    if (graded !== "WIN" && graded !== "LOSS") continue;
    if ((r.bet_placed || "").trim().toUpperCase() !== "Y") continue;
    const side = (r.pick_side || "").trim();
    if (side !== "YRFI" && side !== "NRFI") continue;
    const modelP = num(r.nrfi_prob);
    if (modelP == null) continue;
    const odds = num(side === "YRFI" ? r.market_yrfi_odds : r.market_nrfi_odds);
    const unitsRisked = num(r.units_risked);
    const pnl = num(r.profit_loss_units);
    if (odds == null || odds === 0 || unitsRisked == null || pnl == null) {
      unpricedNights.add((r.date || "").trim());
      continue;
    }
    all.push({
      date: (r.date || "").trim(),
      game: `${(r.away_team || "").trim()}@${(r.home_team || "").trim()}`,
      side, modelP, odds, win: graded === "WIN", unitsRisked, pnl,
    });
  }
  if (all.length === 0) return null;

  // One bet per night: the strongest.
  const byDay = new Map<string, TopPickBet[]>();
  for (const b of all) {
    const list = byDay.get(b.date);
    if (list) list.push(b);
    else byDay.set(b.date, [b]);
  }
  const tops: TopPickBet[] = [];
  for (const d of [...byDay.keys()].sort()) {
    const list = byDay.get(d)!;
    tops.push(list.reduce(better));
  }

  const end = tops[tops.length - 1].date;
  const windows: TopPickWindow[] = [];
  for (const w of WINDOWS) {
    const start = w.days == null ? "" : isoMinus(end, w.days - 1);
    const sel = tops.filter((b) => b.date >= start);
    if (sel.length === 0) continue;
    const n = sel.length;
    const wins = sel.filter((b) => b.win).length;
    const staked = sel.reduce((a, b) => a + b.unitsRisked, 0);
    const returned = sel.reduce((a, b) => a + b.pnl, 0);
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
    const staked = sel.reduce((a, b) => a + b.unitsRisked, 0);
    const returned = sel.reduce((a, b) => a + b.pnl, 0);
    let bank = 100;
    for (const b of sel) bank *= 1 + b.pnl / 100;
    return {
      key,
      bets: n,
      wins,
      losses: n - wins,
      hitRate: n > 0 ? wins / n : 0,
      breakEven: n > 0 ? sel.reduce((a, b) => a + implied(b.odds), 0) / n : 0,
      staked,
      bankEnd: bank,
      ret: bank / 100 - 1,
      roiPerUnit: staked > 0 ? returned / staked : 0,
    };
  };

  let bank = 100;
  let peak = 100;
  let maxDd = 0;
  for (const b of tops) {
    bank *= 1 + b.pnl / 100;
    if (bank > peak) peak = bank;
    const dd = bank / peak - 1;
    if (dd < maxDd) maxDd = dd;
  }

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
    bank: { start: 100, end: bank, ret: bank / 100 - 1, peak, maxDrawdown: maxDd },
    totals: {
      atFlat1u: tops.reduce((a, b) => a + (b.win ? payout(b.odds) : -1), 0),
      atPublishedStakes: tops.reduce((a, b) => a + b.pnl, 0),
    },
    byMonth: [...months.keys()].sort().map((m) => slice(months.get(m)!, m)),
    bySide: (["YRFI", "NRFI"] as const)
      .map((s) => slice(tops.filter((b) => b.side === s), s))
      .filter((s) => s.bets > 0),
    all: [...tops].reverse(),
  };
}
