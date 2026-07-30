import fs from "node:fs/promises";
import path from "node:path";
import { loadRoi } from "@/lib/roi";
import type { RecFile } from "@/lib/season-record";
import { HistoryView } from "@/components/HistoryView";
import { loadTopPickReport } from "@/lib/top-pick";
import { loadThresholds } from "@/lib/thresholds";

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
  /* The #1-pick report reads the LEDGER (real bets, real prices, real
     stakes) while seasonRecord is the REPLAY. Loaded side by side on
     purpose: the card that shows them apart is only honest if they come
     from genuinely different sources. Soft-fails to null. */
  const [seasonRoi, seasonRecord, topPick, thresholds] = await Promise.all([
    loadRoi("season"),
    loadSeasonRecord(),
    loadTopPickReport(new Date().getUTCFullYear()).catch(() => null),
    /* The LIVE gate, so the replay cards can say when they are behind
       it. thresholds.json is written by the predictor; season_record
       carries the gate its replay was actually run at. When the two
       disagree the figures on screen are already superseded. */
    loadThresholds().catch(() => null),
  ]);
  return (
    <HistoryView
      initial={seasonRoi}
      seasonRecord={seasonRecord}
      topPick={topPick}
      liveGate={thresholds?.strongYrfiP ?? null}
    />
  );
}
