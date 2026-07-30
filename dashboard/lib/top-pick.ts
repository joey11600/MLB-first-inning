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

export interface TopPickReport {
  windows: TopPickWindow[];
  /** Most recent first — the run of results behind the headline. */
  recent: TopPickBet[];
  generatedFor: string;
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

  return {
    windows,
    recent: tops.slice(-12).reverse(),
    generatedFor: end,
  };
}
