import fs from "node:fs/promises";
import path from "node:path";
import { loadRoi } from "@/lib/roi";
import type { RecFile } from "@/lib/season-record";
import { HistoryView } from "@/components/HistoryView";

export const dynamic = "force-dynamic";

/** data/season_record.json, or null.
 *
 *  Same two-candidate resolution as /api/season-record: the in-app copy
 *  a built deployment ships, then the repo root when running from
 *  source. Read server-side so the system summary renders on first
 *  paint rather than after a client round-trip.
 *
 *  Soft-fails to null: this page's ledger content does not depend on the
 *  replay, so a missing or malformed record costs one card, never the
 *  page. */
async function loadSeasonRecord(): Promise<RecFile | null> {
  const candidates = [
    path.resolve(process.cwd(), "data", "season_record.json"),
    path.resolve(process.cwd(), "..", "data", "season_record.json"),
  ];
  for (const p of candidates) {
    try {
      return JSON.parse(await fs.readFile(p, "utf8")) as RecFile;
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

export default async function HistoryPage() {
  // Pull the season window directly so the page renders SSR-fresh.
  const [seasonRoi, seasonRecord] = await Promise.all([
    loadRoi("season"),
    loadSeasonRecord(),
  ]);
  return <HistoryView initial={seasonRoi} seasonRecord={seasonRecord} />;
}
