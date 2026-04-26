import { NextResponse } from "next/server";
import { loadBoard } from "@/lib/board";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const date = searchParams.get("date");
  const data = await loadBoard(date);
  return NextResponse.json(data, {
    headers: { "Cache-Control": "no-store" },
  });
}
