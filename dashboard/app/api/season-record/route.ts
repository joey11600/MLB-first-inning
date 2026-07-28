import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * /api/season-record -- serves data/season_record.json verbatim.
 *
 * The file is written by tools/export_season_record.py (walk-forward
 * replay of the current model + staking) and copied into dashboard/data
 * by scripts/copy-data.mjs at build time. Same two-candidate path
 * resolution as every other data reader in this app: the in-app copy
 * first (what a built deployment ships), then the repo root (running
 * from source).
 */
export async function GET() {
  const candidates = [
    path.resolve(process.cwd(), "data", "season_record.json"),
    path.resolve(process.cwd(), "..", "data", "season_record.json"),
  ];
  for (const p of candidates) {
    try {
      const raw = await fs.readFile(p, "utf8");
      return new NextResponse(raw, {
        headers: {
          "content-type": "application/json",
          "Cache-Control": "no-store",
        },
      });
    } catch {
      /* try next candidate */
    }
  }
  return NextResponse.json({ available: false }, { status: 404 });
}
