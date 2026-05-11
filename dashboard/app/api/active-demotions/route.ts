import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { parseCsv } from "@/lib/csv";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * /api/active-demotions -- summarise currently-active cluster demotions
 * for the dashboard banner.
 *
 * Reads `data/cluster_demotions.json` and counts how many of today's +
 * trailing-7d picks were demoted under each active rule (via the
 * "PASS - Cluster demotion:" pick_label prefix that
 * tools/apply_cluster_demotion.py stamps).
 *
 * Response:
 * {
 *   demotions: [
 *     {
 *       id: "thin_pitcher_strong_v1",
 *       reason: "...",
 *       reevaluateAfter: "2026-05-14" | null,
 *       daysUntilReeval: 3 | -1 (-1 means past due) | null,
 *       todayCount: number,
 *       last7Count: number,
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
  }).format(new Date());   // returns "YYYY-MM-DD"
}

function daysBetween(fromIso: string, toIso: string): number {
  const a = new Date(`${fromIso}T00:00:00Z`).getTime();
  const b = new Date(`${toIso}T00:00:00Z`).getTime();
  return Math.round((a - b) / (24 * 60 * 60 * 1000));
}

interface Demotion {
  id: string;
  reason?: string;
  active?: boolean;
  reevaluate_after?: string;
}

export async function GET() {
  const dir = dataDir();
  const today = todayEt();
  const sevenDaysAgo = (() => {
    const d = new Date(`${today}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() - 6);
    return d.toISOString().slice(0, 10);
  })();

  let demotions: Demotion[] = [];
  try {
    const raw = await fs.readFile(path.join(dir, "cluster_demotions.json"), "utf8");
    const parsed = JSON.parse(raw) as { demotions?: Demotion[] };
    demotions = (parsed.demotions || []).filter((d) => d.active !== false);
  } catch {
    return NextResponse.json({ demotions: [] });
  }
  if (demotions.length === 0) {
    return NextResponse.json({ demotions: [] });
  }

  // Count demoted rows in today's slate + trailing-7d for each id.
  const year = today.slice(0, 4);
  const csvPath = path.join(dir, `picks_${year}.csv`);
  let todayByDem: Record<string, number> = {};
  let last7ByDem: Record<string, number> = {};
  try {
    const csv = await fs.readFile(csvPath, "utf8");
    const rows = parseCsv(csv) as Array<Record<string, string>>;
    for (const r of rows) {
      const date = r.date || "";
      const label = r.pick_label || "";
      if (!label.startsWith("PASS - Cluster demotion:")) continue;
      // Extract the id from "(id)" at the end
      const m = label.match(/\(([^)]+)\)\s*$/);
      if (!m) continue;
      const id = m[1];
      if (date === today) todayByDem[id] = (todayByDem[id] || 0) + 1;
      if (date >= sevenDaysAgo && date <= today) {
        last7ByDem[id] = (last7ByDem[id] || 0) + 1;
      }
    }
  } catch {
    /* csv missing -- counts stay 0 */
  }

  const enriched = demotions.map((d) => {
    let daysUntilReeval: number | null = null;
    if (d.reevaluate_after) {
      daysUntilReeval = daysBetween(d.reevaluate_after, today);
    }
    return {
      id: d.id,
      reason: d.reason || "",
      reevaluateAfter: d.reevaluate_after || null,
      daysUntilReeval,
      todayCount: todayByDem[d.id] || 0,
      last7Count: last7ByDem[d.id] || 0,
    };
  });

  return NextResponse.json({ demotions: enriched });
}
