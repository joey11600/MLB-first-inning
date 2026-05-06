import { NextResponse } from "next/server";
import { loadRoi, type RoiWindow } from "@/lib/roi";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * /api/roi -- bankroll aggregation endpoint.
 *
 * Query params:
 *   window  7d | 30d | season           (default: 30d)
 *   date    YYYY-MM-DD reference date   (default: today)
 *
 * Aggregates picks_<year>.csv on disk for the V2.1 production model.
 *
 * "today" is normally aggregated client-side by RoiPanel from the rows
 * + details it already has in memory (lib/roi-today.ts), so this
 * endpoint sees today only when something else explicitly queries it.
 * loadRoi honors "today" as a 1-day window for parity.
 *
 * T-V21-LOCKIN-2026-05-06: removed `model` query param + V3 dispatch
 * (Variant K shadow).  V2.1 is the only production model surfaced.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const rawWindow = (searchParams.get("window") ?? "30d").toLowerCase();
  const window: RoiWindow =
    rawWindow === "today"  ? "today"
    : rawWindow === "7d"     ? "7d"
    : rawWindow === "season" ? "season"
    : "30d";
  const date = searchParams.get("date") ?? undefined;

  const data = await loadRoi(window, date);

  return NextResponse.json(data, {
    headers: { "Cache-Control": "no-store" },
  });
}
