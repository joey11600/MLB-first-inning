import { NextResponse } from "next/server";
import { loadRoi, loadV3Roi, type RoiWindow } from "@/lib/roi";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * /api/roi -- bankroll aggregation endpoint.
 *
 * Query params:
 *   window  7d | 30d | season           (default: 30d)
 *   date    YYYY-MM-DD reference date   (default: today)
 *   model   v2 | v3                     (default: v2)
 *
 * v2 is the production calibrator -- aggregates picks_<year>.csv on disk.
 * v3 is the experimental Variant K shadow (T3.19) -- aggregates Supabase
 * pick_variants WHERE variant_name='K'.  T3.23: v3 dispatch landed; the
 * frontend's RoiPanel now shows real v3 stats for 7d/30d/season instead
 * of falling back to v2 numbers.
 *
 * "today" is normally aggregated client-side by RoiPanel from the rows
 * + details it already has in memory (lib/roi-today.ts), so this
 * endpoint sees today only when something else explicitly queries it.
 * loadRoi + loadV3Roi both honor "today" as a 1-day window for parity.
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
  const model = (searchParams.get("model") ?? "v2").toLowerCase() === "v3"
    ? "v3"
    : "v2";

  const data = model === "v3"
    ? await loadV3Roi(window, date)
    : await loadRoi(window, date);

  return NextResponse.json(data, {
    headers: { "Cache-Control": "no-store" },
  });
}
