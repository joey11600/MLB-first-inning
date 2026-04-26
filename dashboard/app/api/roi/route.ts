import { NextResponse } from "next/server";
import { loadRoi, type RoiWindow } from "@/lib/roi";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const rawWindow = (searchParams.get("window") ?? "30d").toLowerCase();
  const window: RoiWindow =
    rawWindow === "7d" || rawWindow === "season" ? rawWindow : "30d";
  const date = searchParams.get("date") ?? undefined;

  const data = await loadRoi(window, date);
  return NextResponse.json(data, {
    headers: { "Cache-Control": "no-store" },
  });
}
