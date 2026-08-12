import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";

import { todayEtIso } from "@/lib/date";
import { loadLedgerRows } from "@/lib/roi";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * /api/calibration -- the reliability curve's data (T8.34).
 *
 * WHY THIS FILE DID NOT EXIST UNTIL NOW.  `<ReliabilityCurve />` has
 * been mounted in DashboardShell since the 2026-07-28 chart spec and
 * self-fetches this endpoint. The route was never written, so the fetch
 * 404'd, the component's `.catch()` set state to "error", and its
 * render returned `null` -- by design: "a missing diagnostic is a
 * zero-pixel outcome, not an error surface." The chart therefore failed
 * SILENTLY for two weeks. The only evidence was a 404 in the browser
 * console, which is how it was finally noticed.
 *
 * Read-only. Touches no pick, no stake and no ledger column.
 *
 * WHAT IT MEASURES.  Not the betting record -- the MODEL's honesty. For
 * every graded first inning of the season it bins games by the model's
 * P(YRFI) and reports how often a run actually scored in that bin. A
 * well-calibrated model sits on the diagonal: the games it calls 60%
 * should come in about 60% of the time. This is deliberately EVERY
 * graded game, not just the bets, because a calibration curve drawn
 * only over games we bet is drawn over a selected sample and would
 * flatter itself.
 *
 * `breakEven` is the other line on that chart: the mean implied
 * probability of the DK prices actually paid on placed YRFI bets. The
 * gap between the curve and that line is the edge, if there is one.
 * Restricted to the YRFI side because the chart's axis is P(YRFI) and
 * an NRFI price would have to be mirrored onto it to compare -- and
 * STRONG NRFI betting is disabled anyway.
 *
 * DATA SOURCE is `loadLedgerRows`, the same Supabase-then-CSV reader
 * /history and the No.1 tracker use. It is paginated: PostgREST caps a
 * read at 1000 rows and a season is ~1700+, so a naive read would
 * silently drop most of it -- the cap that has already truncated
 * pl_calc and the date picker.
 *
 * NEVER INVENT A NUMBER (InsightCharts' own rule #1). Bins thinner than
 * MIN_BIN_N are dropped rather than plotted as a dot with an invisible
 * sample behind it; the count of dropped bins is reported so the chart
 * can say so. Wilson bounds are deliberately NOT sent -- the component
 * derives them locally, and one implementation cannot disagree with
 * itself.
 */

/** Bin width on the P(YRFI) axis. 0.05 puts ~9 populated bins across
 *  the range the model actually uses (0.30-0.75), which is enough shape
 *  to read without pretending to a resolution 1700 games cannot carry. */
const BIN_WIDTH = 0.05;

/** A bin needs this many games to be plotted. At n=20 the Wilson band is
 *  still about +-20 points, which is honest; at n=9 (the top bin today)
 *  it is +-30 and the dot reads as signal when it is noise. */
const MIN_BIN_N = 20;

function isFiniteNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function num(v: string | undefined | null): number | null {
  const s = (v ?? "").trim();
  if (!s) return null;
  const n = Number.parseFloat(s);
  return Number.isFinite(n) ? n : null;
}

/** The STRONG YRFI gate, expressed on the P(YRFI) axis.
 *
 *  `thresholds.json` stores `strongYrfiP` as a P(NRFI) cut -- the
 *  tracker commits STRONG YRFI when `nrfi_prob <= strongYrfiP` -- so the
 *  same boundary on this chart's axis is its complement. Same
 *  two-candidate path resolution as every other data reader here: the
 *  in-app copy a built deployment ships, then the repo root. */
async function readGate(): Promise<number | null> {
  const candidates = [
    path.resolve(process.cwd(), "data", "thresholds.json"),
    path.resolve(process.cwd(), "..", "data", "thresholds.json"),
  ];
  for (const p of candidates) {
    try {
      const t = JSON.parse(await fs.readFile(p, "utf8")) as Record<string, unknown>;
      const s = t.strongYrfiP;
      // Rounded because 1 - 0.42 is 0.5800000000000001 in binary floating
      // point. The chart happens to print it as "58%", but an unrounded
      // gate is the kind of value that reads fine until some later
      // consumer compares it for equality.
      if (isFiniteNum(s) && s > 0 && s < 1) return Math.round((1 - s) * 1e6) / 1e6;
    } catch {
      /* try next candidate */
    }
  }
  return null;
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const seasonParam = Number.parseInt(url.searchParams.get("season") ?? "", 10);
  const season = Number.isFinite(seasonParam)
    ? seasonParam
    : Number(todayEtIso().slice(0, 4));

  const rows = await loadLedgerRows(season);
  if (!rows) {
    return NextResponse.json({ available: false }, { status: 404 });
  }

  const gate = await readGate();

  // ---- the sample: every game with a model probability AND a graded
  // first inning. `actual_result` is written by the grader as exactly
  // "YRFI" or "NRFI" and is the whole outcome, independent of what was
  // picked or whether anything was bet.
  type Game = { p: number; scored: boolean };
  const games: Game[] = [];
  for (const r of rows) {
    const p = num(r.yrfi_prob);
    const outcome = (r.actual_result ?? "").trim().toUpperCase();
    if (p == null || p < 0 || p > 1) continue;
    if (outcome !== "YRFI" && outcome !== "NRFI") continue;
    games.push({ p, scored: outcome === "YRFI" });
  }

  if (games.length === 0) {
    return NextResponse.json({ available: false }, { status: 404 });
  }

  // ---- bins
  const nBins = Math.ceil(1 / BIN_WIDTH);
  const acc = Array.from({ length: nBins }, () => ({ n: 0, sumP: 0, hits: 0 }));
  for (const g of games) {
    const i = Math.min(Math.floor(g.p / BIN_WIDTH), nBins - 1);
    acc[i].n += 1;
    acc[i].sumP += g.p;
    if (g.scored) acc[i].hits += 1;
  }

  let droppedBins = 0;
  const bins = acc
    .map((a, i) => ({ a, i }))
    .filter(({ a }) => {
      if (a.n === 0) return false;        // empty is not "dropped", just absent
      if (a.n < MIN_BIN_N) {
        droppedBins += 1;
        return false;
      }
      return true;
    })
    .map(({ a, i }) => ({
      lo: i * BIN_WIDTH,
      hi: (i + 1) * BIN_WIDTH,
      n: a.n,
      meanPred: a.sumP / a.n,
      actual: a.hits / a.n,
    }));

  // ---- the bet region: every graded game at or above the gate.
  //
  // Computed from the RAW games rather than by summing the bins above,
  // so a suppressed thin bin still counts here. That is the point of an
  // aggregate -- the top bin is exactly where the STRONG plays live, and
  // it is the one most likely to be too thin to plot on its own.
  let betRegion: { n: number; pred: number; actual: number } | undefined;
  if (gate != null) {
    const inRegion = games.filter((g) => g.p >= gate);
    if (inRegion.length > 0) {
      betRegion = {
        n: inRegion.length,
        pred: inRegion.reduce((s, g) => s + g.p, 0) / inRegion.length,
        actual: inRegion.filter((g) => g.scored).length / inRegion.length,
      };
    }
  }

  // ---- break-even: the prices actually paid.
  //
  // Placed YRFI bets carrying a captured implied probability. NEVER the
  // flat -110 fallback `_calc_pnl` uses for an unpriced row: that price
  // was never real, and averaging it in would move this line toward a
  // number nobody was ever offered.
  const implied: number[] = [];
  for (const r of rows) {
    if ((r.bet_placed ?? "").trim().toUpperCase() !== "Y") continue;
    if ((r.pick_side ?? "").trim().toUpperCase() !== "YRFI") continue;
    const q = num(r.implied_yrfi_prob);
    if (q != null && q > 0 && q < 1) implied.push(q);
  }
  const breakEven =
    implied.length > 0
      ? implied.reduce((s, q) => s + q, 0) / implied.length
      : null;

  return NextResponse.json(
    {
      bins,
      breakEven,
      gate,
      betRegion,
      totalGames: games.length,
      binWidth: BIN_WIDTH,
      minBinN: MIN_BIN_N,
      droppedBins,
      breakEvenBets: implied.length,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
