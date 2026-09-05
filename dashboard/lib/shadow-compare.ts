/**
 * The SHADOW model against the live one, paired by night.
 *
 * Since 2026-09-04 the predictor scores every game twice and records the
 * second model's opinion in the ledger's `shadow_*` columns (never
 * published, never bet). This module turns those rows into the comparison
 * the operator asked for -- "run an alternate version side by side" -- and
 * it is a MIRROR of `tools/shadow_report.py`: same filters, same sizing rule,
 * same definition of the night's No.1. Where the two disagree the Python
 * report is the authority, because the ledger is the record; this only
 * displays.
 *
 * PURE. No `node:fs`, no Supabase: it takes the ledger rows it is handed
 * (the history page loads them server-side through `loadLedgerRows` and
 * passes the finished report down), so it can be imported from a client
 * component without dragging the filesystem into the browser bundle -- the
 * exact failure `lib/top-pick-rank.ts` exists to avoid.
 *
 * THREE LINES PER MODEL, deliberately:
 *   booked     the live model's real stakes and P&L from the ledger; only
 *              the live model has this
 *   same-rule  BOTH models sized by one rule -- quarter Kelly on the
 *              model's own probability at the captured YRFI price, via the
 *              same `stakeUnitsFor` the board uses -- with no daily cap or
 *              lock-order effects, so the two are compared on identical
 *              sizing
 *   flat 1u    the hit rate priced without any sizing at all
 * Every sum here is a FIXED-basis total (a unit is a unit on every night),
 * so it is `FlatUnits`, never `CumulativeUnits`.
 *
 * Read the result with the 2026-08-13 fortnight review in mind: a few weeks
 * of nights cannot settle which model is better. What accumulates is proof
 * the candidate computes live and looks sane, plus paired rows for the
 * offseason decision.
 */
import { pnlUnitsFor, stakeUnitsFor } from "./kelly-sim";
import { impliedFromOdds } from "./top-pick-rank";
import { asFlat, type FlatUnits } from "./units";

/** Profit per 1u at an American price (kelly-sim keeps its copy private). */
function payoutPerUnit(american: number): number {
  return american > 0 ? american / 100 : 100 / -american;
}

export interface ShadowBet {
  date: string;
  game: string;
  /** p(YRFI) the model stated. */
  p: number;
  odds: number;
  won: boolean;
  stake: number;
  pnl: number;
}

export interface ShadowSide {
  bets: number;
  wins: number;
  losses: number;
  hit: number | null;
  stated: number | null;
  breakEven: number | null;
  flat: FlatUnits;
  sameRule: FlatUnits;
  staked: number;
}

export interface ShadowNight {
  date: string;
  live: { game: string; won: boolean; pnl: number } | null;
  shadow: { game: string; won: boolean; pnl: number } | null;
  sameGame: boolean;
}

export interface ShadowNo1 {
  nights: number;
  wins: number;
  hit: number | null;
  sameRule: FlatUnits;
}

export interface ShadowReport {
  since: string;
  model: string;
  gradedRows: number;
  live: ShadowSide & { booked: FlatUnits; bookedStaked: number };
  shadow: ShadowSide;
  no1: {
    live: ShadowNo1;
    shadow: ShadowNo1;
    nightsBoth: number;
    liveOnBoth: ShadowNo1;
    shadowOnBoth: ShadowNo1;
    sameGameOnBoth: number;
  };
  agreement: { key: string; count: number }[];
  nights: ShadowNight[];
  tonight: { date: string | null; live: string[]; shadow: string[] };
}

function num(v: string | undefined): number | null {
  if (v == null) return null;
  const s = String(v).trim();
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** ("STRONG" | "LEAN" | "PASS", "YRFI" | "NRFI" | "") -- same as the Python. */
function sideOf(label: string | undefined): [string, string] {
  const s = (label ?? "").trim().toUpperCase();
  if (s.startsWith("STRONG")) return ["STRONG", s.includes("YRFI") ? "YRFI" : "NRFI"];
  if (s.startsWith("LEAN")) return ["LEAN", s.includes("YRFI") ? "YRFI" : "NRFI"];
  return ["PASS", ""];
}

function summarize(bets: ShadowBet[]): ShadowSide {
  const n = bets.length;
  const wins = bets.filter((b) => b.won).length;
  const flat = bets.reduce((a, b) => a + (b.won ? payoutPerUnit(b.odds) : -1), 0);
  return {
    bets: n,
    wins,
    losses: n - wins,
    hit: n ? wins / n : null,
    stated: n ? bets.reduce((a, b) => a + b.p, 0) / n : null,
    breakEven: n ? bets.reduce((a, b) => a + impliedFromOdds(b.odds), 0) / n : null,
    flat: asFlat(flat),
    sameRule: asFlat(bets.reduce((a, b) => a + b.pnl, 0)),
    staked: bets.reduce((a, b) => a + b.stake, 0),
  };
}

/** The night's No.1 = highest p(YRFI), the better price breaking ties. */
function no1ByNight(bets: ShadowBet[]): Map<string, ShadowBet> {
  const best = new Map<string, ShadowBet>();
  for (const b of bets) {
    const cur = best.get(b.date);
    if (
      !cur ||
      b.p > cur.p ||
      (b.p === cur.p && impliedFromOdds(b.odds) < impliedFromOdds(cur.odds))
    ) {
      best.set(b.date, b);
    }
  }
  return best;
}

function no1Summary(sel: Map<string, ShadowBet>, only?: Set<string>): ShadowNo1 {
  const xs = [...sel.entries()].filter(([d]) => !only || only.has(d)).map(([, b]) => b);
  const wins = xs.filter((b) => b.won).length;
  return {
    nights: xs.length,
    wins,
    hit: xs.length ? wins / xs.length : null,
    sameRule: asFlat(xs.reduce((a, b) => a + b.pnl, 0)),
  };
}

/**
 * Build the comparison from raw ledger rows (CSV-shaped strings, as
 * `loadLedgerRows` returns them). Returns null when no row carries a shadow
 * value yet -- the section then says so instead of showing zeros.
 */
export function buildShadowReport(
  rows: Record<string, string>[],
  since?: string,
): ShadowReport | null {
  const shadowDates = rows
    .filter((r) => (r.shadow_model ?? "").trim() !== "")
    .map((r) => (r.date ?? "").slice(0, 10))
    .filter(Boolean)
    .sort();
  if (shadowDates.length === 0) return null;
  const start = since ?? shadowDates[0];
  const model = rows.find((r) => (r.shadow_model ?? "").trim() !== "")?.shadow_model?.trim() ?? "";

  const inWindow = rows.filter((r) => (r.date ?? "").slice(0, 10) >= start);
  const graded = inWindow.filter((r) => {
    const g = (r.graded_result ?? "").trim().toUpperCase();
    return (g === "WIN" || g === "LOSS" || g === "PASS") && num(r.fi_total_runs) != null;
  });

  const liveBets: (ShadowBet & { bookedStake: number; bookedPnl: number })[] = [];
  const shadowBets: ShadowBet[] = [];
  const agree = new Map<string, number>();

  for (const r of graded) {
    const yRun = (num(r.fi_total_runs) ?? 0) > 0;
    const odds = num(r.market_yrfi_odds);
    const [ls, lside] = sideOf(r.pick_label);
    const [ss, sside] = sideOf(r.shadow_pick_label);
    const key = `live ${ls}${lside ? " " + lside : ""} · shadow ${ss}${sside ? " " + sside : ""}`;
    agree.set(key, (agree.get(key) ?? 0) + 1);
    const game = `${r.away_team ?? ""}@${r.home_team ?? ""}`;
    const date = (r.date ?? "").slice(0, 10);

    if ((r.bet_placed ?? "").trim().toUpperCase() === "Y" && lside === "YRFI" && odds != null) {
      const p = 1 - (num(r.nrfi_prob) ?? 0.5);
      liveBets.push({
        date, game, p, odds, won: yRun,
        stake: stakeUnitsFor(p, odds),
        pnl: pnlUnitsFor(p, odds, yRun),
        bookedStake: num(r.units_risked) ?? 0,
        bookedPnl: num(r.profit_loss_units) ?? 0,
      });
    }
    if (ss === "STRONG" && sside === "YRFI" && odds != null) {
      const sp = num(r.shadow_nrfi_prob);
      if (sp != null) {
        const p = 1 - sp;
        shadowBets.push({
          date, game, p, odds, won: yRun,
          stake: stakeUnitsFor(p, odds),
          pnl: pnlUnitsFor(p, odds, yRun),
        });
      }
    }
  }

  const liveNo1 = no1ByNight(liveBets);
  const shadowNo1 = no1ByNight(shadowBets);
  const nightKeys = [...new Set([...liveNo1.keys(), ...shadowNo1.keys()])].sort();
  const both = new Set(nightKeys.filter((d) => liveNo1.has(d) && shadowNo1.has(d)));
  const nights: ShadowNight[] = nightKeys.map((d) => {
    const a = liveNo1.get(d) ?? null;
    const b = shadowNo1.get(d) ?? null;
    return {
      date: d,
      live: a ? { game: a.game, won: a.won, pnl: a.pnl } : null,
      shadow: b ? { game: b.game, won: b.won, pnl: b.pnl } : null,
      sameGame: !!(a && b && a.game === b.game),
    };
  });

  const ungraded = inWindow.filter((r) => (r.graded_result ?? "").trim() === "");
  const tonightDate = ungraded.map((r) => (r.date ?? "").slice(0, 10)).sort().at(-1) ?? null;
  const tonightRows = tonightDate ? ungraded.filter((r) => (r.date ?? "").slice(0, 10) === tonightDate) : [];
  const strongYrfi = (label: string | undefined) => {
    const [s, side] = sideOf(label);
    return s === "STRONG" && side === "YRFI";
  };

  const liveSide = summarize(liveBets);
  return {
    since: start,
    model,
    gradedRows: graded.length,
    live: {
      ...liveSide,
      booked: asFlat(liveBets.reduce((a, b) => a + b.bookedPnl, 0)),
      bookedStaked: liveBets.reduce((a, b) => a + b.bookedStake, 0),
    },
    shadow: summarize(shadowBets),
    no1: {
      live: no1Summary(liveNo1),
      shadow: no1Summary(shadowNo1),
      nightsBoth: both.size,
      liveOnBoth: no1Summary(liveNo1, both),
      shadowOnBoth: no1Summary(shadowNo1, both),
      sameGameOnBoth: nights.filter((n) => n.sameGame).length,
    },
    agreement: [...agree.entries()]
      .map(([key, count]) => ({ key, count }))
      .sort((a, b) => b.count - a.count),
    nights,
    tonight: {
      date: tonightDate,
      live: tonightRows.filter((r) => strongYrfi(r.pick_label)).map((r) => `${r.away_team}@${r.home_team}`),
      shadow: tonightRows.filter((r) => strongYrfi(r.shadow_pick_label)).map((r) => `${r.away_team}@${r.home_team}`),
    },
  };
}
