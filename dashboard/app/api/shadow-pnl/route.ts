import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { parseCsv } from "@/lib/csv";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * /api/shadow-pnl -- summarise active cluster demotions' shadow P&L
 * for the dashboard card.  Mirrors the logic in
 * `tools/cluster_shadow_pnl.py` so the UI shows the same numbers as
 * the CLI tool / Telegram reminder.
 *
 * For each active demotion, splits matching graded rows since the
 * demotion turned on (operator's `reevaluate_after - some lookback`,
 * or season start as fallback) into:
 *
 *   REAL    -- bet_placed=Y rows whose feature shape matches the
 *              predicate AND pre-date the demotion going live.
 *              Use stored profit_loss_units.
 *   SHADOW  -- cluster-demoted rows (pick_label has "PASS - Cluster
 *              demotion: ...") matching the predicate.  Compute
 *              hypothetical 1u P&L from actual_result + captured
 *              opened_*_odds (or flat -110 fallback).
 *
 * Response:
 * {
 *   clusters: [
 *     {
 *       id: "thin_pitcher_strong_v1",
 *       real: { n, wins, losses, pnl },
 *       shadow: { n, wins, losses, pnl },
 *       total: { n, wins, losses, pnl },
 *     },
 *     ...
 *   ]
 * }
 */

function dataDir(): string {
  const local = path.resolve(process.cwd(), "data");
  const parent = path.resolve(process.cwd(), "..", "data");
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fsSync = require("node:fs") as typeof import("node:fs");
    if (fsSync.existsSync(path.join(parent, "boards"))) return parent;
    if (fsSync.existsSync(path.join(local, "boards"))) return local;
  } catch {
    /* ignore */
  }
  return parent;
}

function todayEt(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function safeFloat(s: string | undefined | null, d = NaN): number {
  if (s == null) return d;
  const t = String(s).trim();
  if (!t) return d;
  const n = Number(t);
  return Number.isFinite(n) ? n : d;
}

function payoutPerUnit(odds: string | undefined | null): number | null {
  if (!odds) return null;
  const n = safeFloat(odds);
  if (!Number.isFinite(n) || n === 0) return null;
  if (n > 0) return n / 100;
  return 100 / Math.abs(n);
}

interface Demotion {
  id: string;
  side?: string | null;
  nrfi_prob?: { min?: number | null; max?: number | null };
  combined_lambda?: { min?: number | null; max?: number | null };
  park_factor?: { min?: number | null; max?: number | null };
  pitcher_quality_min?: string[] | null;
  active?: boolean;
}

function pitcherMinQ(aq: string, hq: string): string {
  const order: Record<string, number> = { sm: 0, ltd: 1, live: 2 };
  const a = order[(aq || "").toLowerCase()] ?? -1;
  const h = order[(hq || "").toLowerCase()] ?? -1;
  const m = Math.min(a, h);
  const rev: Record<number, string> = { 0: "sm", 1: "ltd", 2: "live", [-1]: "avg" };
  return rev[m] || "avg";
}

function matches(row: Record<string, string>, dem: Demotion, effSide: string): boolean {
  if (dem.side && effSide.toUpperCase() !== dem.side.toUpperCase()) return false;
  const p = safeFloat(row.nrfi_prob);
  const lam = safeFloat(row.combined_lambda);
  const park = safeFloat(row.park_factor);
  const np = dem.nrfi_prob || {};
  if (np.min != null && (!Number.isFinite(p) || p < np.min)) return false;
  if (np.max != null && (!Number.isFinite(p) || p > np.max)) return false;
  const lb = dem.combined_lambda || {};
  if (lb.min != null && (!Number.isFinite(lam) || lam < lb.min)) return false;
  if (lb.max != null && (!Number.isFinite(lam) || lam > lb.max)) return false;
  const pb = dem.park_factor || {};
  if (pb.min != null && (!Number.isFinite(park) || park < pb.min)) return false;
  if (pb.max != null && (!Number.isFinite(park) || park > pb.max)) return false;
  if (dem.pitcher_quality_min && dem.pitcher_quality_min.length > 0) {
    const pq = pitcherMinQ(row.away_pitcher_q || "", row.home_pitcher_q || "");
    if (!dem.pitcher_quality_min.includes(pq)) return false;
  }
  return true;
}

function effectiveSide(row: Record<string, string>): string {
  const side = (row.pick_side || "").toUpperCase();
  if (side === "NRFI" || side === "YRFI") return side;
  const label = row.pick_label || "";
  const m = label.match(/STRONG (NRFI|YRFI)/);
  return m ? m[1] : "";
}

function isDemoted(row: Record<string, string>): boolean {
  return (row.pick_label || "").startsWith("PASS - Cluster demotion:");
}

function shadowPnlForRow(row: Record<string, string>, side: string, grade: string): number {
  if (grade === "LOSS") return -1.0;
  if (grade !== "WIN") return 0.0;
  const openedCol = side === "NRFI" ? "opened_nrfi_odds" : "opened_yrfi_odds";
  const marketCol = side === "NRFI" ? "market_nrfi_odds" : "market_yrfi_odds";
  const ppu = payoutPerUnit(row[openedCol]) ?? payoutPerUnit(row[marketCol]) ?? 100 / 110;
  return ppu;
}

function shadowGrade(row: Record<string, string>, side: string): string {
  const grade = (row.graded_result || "").toUpperCase();
  if (grade === "WIN" || grade === "LOSS") return grade;
  const actual = (row.actual_result || "").toUpperCase();
  if ((actual === "NRFI" || actual === "YRFI") && (side === "NRFI" || side === "YRFI")) {
    return actual === side ? "WIN" : "LOSS";
  }
  return "";
}

export async function GET() {
  const dir = dataDir();
  const today = todayEt();
  let demotions: Demotion[] = [];
  try {
    const raw = await fs.readFile(path.join(dir, "cluster_demotions.json"), "utf8");
    const parsed = JSON.parse(raw) as { demotions?: Demotion[] };
    demotions = (parsed.demotions || []).filter((d) => d.active !== false);
  } catch {
    return NextResponse.json({ clusters: [] });
  }
  if (demotions.length === 0) {
    return NextResponse.json({ clusters: [] });
  }

  const year = today.slice(0, 4);
  let rows: Array<Record<string, string>> = [];
  try {
    const csv = await fs.readFile(path.join(dir, `picks_${year}.csv`), "utf8");
    rows = parseCsv(csv) as Array<Record<string, string>>;
  } catch {
    return NextResponse.json({ clusters: demotions.map((d) => ({
      id: d.id,
      real:   { n: 0, wins: 0, losses: 0, pnl: 0 },
      shadow: { n: 0, wins: 0, losses: 0, pnl: 0 },
      total:  { n: 0, wins: 0, losses: 0, pnl: 0 },
    })) });
  }

  const out = demotions.map((dem) => {
    const real:   { n: number; wins: number; losses: number; pnl: number } = { n: 0, wins: 0, losses: 0, pnl: 0 };
    const shadow: { n: number; wins: number; losses: number; pnl: number } = { n: 0, wins: 0, losses: 0, pnl: 0 };
    for (const r of rows) {
      const side = effectiveSide(r);
      if (!side) continue;
      const demoted = isDemoted(r);
      // For predicate matching we treat the demoted row as if its
      // pick_side were still the original (the demotion's predicate
      // checks side; we want to know "would this row have matched
      // back then?").
      const synthSide = side;
      if (!matches(r, dem, synthSide)) continue;
      const grade = shadowGrade(r, side);
      if (grade !== "WIN" && grade !== "LOSS") continue;
      if (demoted) {
        const pl = shadowPnlForRow(r, side, grade);
        shadow.n += 1;
        if (grade === "WIN") shadow.wins += 1;
        else shadow.losses += 1;
        shadow.pnl += pl;
      } else {
        // Real bet placed (pre-demotion or non-matching-bet path)
        if ((r.bet_placed || "").toUpperCase() !== "Y") continue;
        const pl = safeFloat(r.profit_loss_units, 0);
        real.n += 1;
        if (grade === "WIN") real.wins += 1;
        else real.losses += 1;
        real.pnl += pl;
      }
    }
    const total = {
      n: real.n + shadow.n,
      wins: real.wins + shadow.wins,
      losses: real.losses + shadow.losses,
      pnl: real.pnl + shadow.pnl,
    };
    return { id: dem.id, real, shadow, total };
  });

  return NextResponse.json({ clusters: out });
}
