/**
 * GET /api/shadow-summary
 *
 * T4.4 -- daily T4.2 shadow timeline surfaced on the dashboard.
 *
 * Reads data/diagnostics/shadow_summary.csv (copied by scripts/copy-data.mjs
 * from the repo root during the build) and returns:
 *
 *   - last 14 entries (one row per day, newest first)
 *   - trailing 7-day aggregate (sum delta_pl, sum v2_pl, sum t42_pl)
 *   - trailing 14-day aggregate
 *   - status: "ok" | "warn" | "regress" | "unknown"
 *       ok      = positive 7d delta_pl
 *       warn    = 7d delta_pl in [-1u, 0u]  (mild dip)
 *       regress = 7d delta_pl < -1u OR 5+ consecutive negative days
 *       unknown = no data yet (file empty / missing)
 *
 * The card consumes this to show "T4.2 working" or "drift detected"
 * at a glance.  See PLAYBOOK section 3 for the routing logic when
 * regress fires.
 */

import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Resolve the repo-level `data/` dir.  Same pattern as lib/board.ts and
 *  app/api/health/route.ts: on Vercel after copy-data.mjs runs, ./data
 *  exists in the dashboard root; locally we fall back to ../data. */
function dataDir(): string {
  const local  = path.resolve(process.cwd(), "data");
  const parent = path.resolve(process.cwd(), "..", "data");
  try {
    if (fs.existsSync(path.join(parent, "diagnostics", "shadow_summary.csv"))) return parent;
    if (fs.existsSync(path.join(local,  "diagnostics", "shadow_summary.csv"))) return local;
    // Fall back to whichever boards dir exists (matches the rest of the
    // dashboard's resolution; the file may not exist yet but at least we
    // pick a sensible base path).
    if (fs.existsSync(path.join(parent, "boards"))) return parent;
    if (fs.existsSync(path.join(local,  "boards"))) return local;
  } catch {
    /* ignore */
  }
  return local;
}

interface SummaryRow {
  date:      string;
  nBets:     number;
  v2W:       number;
  v2L:       number;
  v2Pl:      number;
  t42W:      number;
  t42L:      number;
  t42Pass:   number;
  t42Pl:     number;
  deltaPl:   number;
  wroteAt:   string;
}

type Status = "ok" | "warn" | "regress" | "unknown";

interface ShadowSummaryResponse {
  status:           Status;
  reason:           string;
  rows:             SummaryRow[];
  last7d: {
    nBets:    number;
    v2Pl:     number;
    t42Pl:    number;
    deltaPl:  number;
    nDays:    number;
  };
  last14d: {
    nBets:    number;
    v2Pl:     number;
    t42Pl:    number;
    deltaPl:  number;
    nDays:    number;
  };
  consecutiveNegativeDays: number;
}

function num(v: string | undefined, d = 0): number {
  if (!v) return d;
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function parseCsv(text: string): SummaryRow[] {
  const lines = text.split(/\r?\n/).filter(l => l.trim().length > 0);
  if (lines.length < 2) return [];
  const header = lines[0].split(",");
  const idx = (name: string) => header.indexOf(name);
  const out: SummaryRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(",");
    if (cells.length < 4) continue;
    out.push({
      date:    cells[idx("date")] || "",
      nBets:   num(cells[idx("n_bets")]),
      v2W:     num(cells[idx("v2_W")]),
      v2L:     num(cells[idx("v2_L")]),
      v2Pl:    num(cells[idx("v2_pl")]),
      t42W:    num(cells[idx("t42_W")]),
      t42L:    num(cells[idx("t42_L")]),
      t42Pass: num(cells[idx("t42_pass")]),
      t42Pl:   num(cells[idx("t42_pl")]),
      deltaPl: num(cells[idx("delta_pl")]),
      wroteAt: cells[idx("wrote_at")] || "",
    });
  }
  // Sort newest first
  out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  return out;
}

function aggregate(rows: SummaryRow[]) {
  return rows.reduce(
    (acc, r) => ({
      nBets:    acc.nBets   + r.nBets,
      v2Pl:     acc.v2Pl    + r.v2Pl,
      t42Pl:    acc.t42Pl   + r.t42Pl,
      deltaPl:  acc.deltaPl + r.deltaPl,
      nDays:    acc.nDays   + 1,
    }),
    { nBets: 0, v2Pl: 0, t42Pl: 0, deltaPl: 0, nDays: 0 },
  );
}

export async function GET() {
  const csvPath = path.join(dataDir(), "diagnostics", "shadow_summary.csv");
  let rows: SummaryRow[] = [];
  try {
    if (fs.existsSync(csvPath)) {
      const text = fs.readFileSync(csvPath, "utf-8");
      rows = parseCsv(text);
    }
  } catch {
    rows = [];
  }

  if (rows.length === 0) {
    const empty: ShadowSummaryResponse = {
      status:                  "unknown",
      reason:                  "No shadow_summary.csv data yet -- first night's grade cron hasn't fired",
      rows:                    [],
      last7d:                  { nBets: 0, v2Pl: 0, t42Pl: 0, deltaPl: 0, nDays: 0 },
      last14d:                 { nBets: 0, v2Pl: 0, t42Pl: 0, deltaPl: 0, nDays: 0 },
      consecutiveNegativeDays: 0,
    };
    return NextResponse.json(empty, { headers: { "cache-control": "no-store" } });
  }

  const last14 = rows.slice(0, 14);
  const last7  = rows.slice(0, 7);
  const agg7   = aggregate(last7);
  const agg14  = aggregate(last14);

  // Count consecutive negative-delta days from the most recent backwards.
  let consecNeg = 0;
  for (const r of rows) {
    if (r.deltaPl < 0) consecNeg++;
    else break;
  }

  let status: Status;
  let reason: string;
  if (agg7.deltaPl > 0) {
    status = "ok";
    reason = `T4.2 producing +${agg7.deltaPl.toFixed(2)}u delta over last ${agg7.nDays} days`;
  } else if (consecNeg >= 5) {
    status = "regress";
    reason = `Negative delta ${consecNeg} consecutive days -- see PLAYBOOK section 3`;
  } else if (agg7.deltaPl < -1) {
    status = "regress";
    reason = `7-day delta ${agg7.deltaPl.toFixed(2)}u (< -1u threshold)`;
  } else {
    status = "warn";
    reason = `7-day delta ${agg7.deltaPl.toFixed(2)}u (mild)`;
  }

  const response: ShadowSummaryResponse = {
    status,
    reason,
    rows:                    last14,
    last7d:                  agg7,
    last14d:                 agg14,
    consecutiveNegativeDays: consecNeg,
  };
  return NextResponse.json(response, { headers: { "cache-control": "no-store" } });
}
