import { NextResponse } from "next/server";

/**
 * /api/live-state?date=YYYY-MM-DD
 *
 * Proxy to MLB Stats API.  Returns simplified per-game live state for the
 * given date so the dashboard can show real-time inning/score without
 * waiting for our cron to grade.  Polled every ~30 seconds by the
 * dashboard during the slate's active hours.
 *
 * Response shape:
 *   {
 *     date: "2026-05-01",
 *     fetchedAt: "2026-05-01T23:45:30Z",
 *     games: [
 *       {
 *         gamePk: 824772,
 *         away: "HOU", home: "BOS",
 *         status: "In Progress" | "Pre-Game" | "Warmup" | "Final" |
 *                 "Postponed" | "Cancelled" | "Delayed Start" | "Delayed",
 *         abstractGameState: "Live" | "Preview" | "Final",
 *         currentInning: 3,
 *         inningState: "Top" | "Middle" | "Bottom" | "End",
 *         awayScore: 1, homeScore: 3,
 *         firstInningComplete: true,   // we can grade NRFI/YRFI now
 *         firstInningTotalRuns: 1,
 *         firstInningAwayRuns: 0,
 *         firstInningHomeRuns: 1,
 *       },
 *       ...
 *     ]
 *   }
 *
 * Cache-Control: no-store so polling always hits fresh.  Server-side
 * proxy avoids CORS issues from talking to MLB API directly from the
 * browser, and lets us shape the payload to exactly what we need (the
 * raw schedule response is ~50KB per call).
 */
export const dynamic = "force-dynamic";
export const revalidate = 0;

const MLB_BASE = "https://statsapi.mlb.com/api/v1";

interface MlbScheduleGame {
  gamePk?: number;
  status?: { detailedState?: string; abstractGameState?: string };
  teams?: {
    away?: { team?: { id?: number; abbreviation?: string }; score?: number };
    home?: { team?: { id?: number; abbreviation?: string }; score?: number };
  };
  linescore?: {
    currentInning?: number;
    inningState?: string;
    innings?: Array<{
      num?: number;
      home?: { runs?: number };
      away?: { runs?: number };
    }>;
  };
}

interface LiveStateGame {
  gamePk: number;
  away: string;
  home: string;
  status: string;
  abstractGameState: string;
  currentInning: number | null;
  inningState: string;
  awayScore: number;
  homeScore: number;
  firstInningComplete: boolean;
  firstInningTotalRuns: number | null;
  firstInningAwayRuns: number | null;
  firstInningHomeRuns: number | null;
}

// Map the schedule response's team abbreviations to our internal abbrs.
// MLB's schedule endpoint returns abbreviation as 3-letter (NYY, BOS, etc).
// We keep them as-is — our internal CSVs use the same convention.

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const date = searchParams.get("date");
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json(
      { error: "missing or invalid date param (expected YYYY-MM-DD)" },
      { status: 400 },
    );
  }

  // Hydrate=linescore gets per-inning runs so we can compute "1st inning
  // complete + runs" without an extra API call per game.
  const url = `${MLB_BASE}/schedule?sportId=1&date=${date}&hydrate=linescore`;

  let data: { dates?: Array<{ date?: string; games?: MlbScheduleGame[] }> } | null = null;
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "nrfi-terminal/1.0 (+vercel)" },
      // Next.js fetch caching: explicitly disable.  MLB updates frequently
      // during games; we never want a stale response.
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        { error: `MLB API ${res.status}`, games: [] },
        { status: 502, headers: { "Cache-Control": "no-store" } },
      );
    }
    data = await res.json();
  } catch (err) {
    return NextResponse.json(
      { error: `fetch failed: ${(err as Error).message}`, games: [] },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }

  const games: LiveStateGame[] = [];
  for (const d of data?.dates ?? []) {
    if (d?.date !== date) continue;
    for (const g of d?.games ?? []) {
      const ls = g.linescore ?? {};
      const inn1 = (ls.innings ?? []).find((i) => i?.num === 1);
      const awayR1 = inn1?.away?.runs;
      const homeR1 = inn1?.home?.runs;
      const status = g.status?.detailedState ?? "";
      const absState = g.status?.abstractGameState ?? "";
      const curInning = ls.currentInning ?? null;
      const innState = ls.inningState ?? "";
      // T1 is "complete" once we're past it -- inning >= 2, OR inning 1
      // and inningState is "Bottom" (T1 complete) or "End" / "Middle"
      // beyond the bottom of 1.
      const firstInningComplete =
        absState === "Final" ||
        (typeof curInning === "number" && curInning >= 2) ||
        (typeof curInning === "number" &&
          curInning === 1 &&
          (innState === "End" || innState === "Middle" || innState === "Bottom"));
      games.push({
        gamePk:           g.gamePk ?? 0,
        away:             g.teams?.away?.team?.abbreviation ?? "?",
        home:             g.teams?.home?.team?.abbreviation ?? "?",
        status,
        abstractGameState: absState,
        currentInning:    curInning,
        inningState:      innState,
        awayScore:        g.teams?.away?.score ?? 0,
        homeScore:        g.teams?.home?.score ?? 0,
        firstInningComplete,
        firstInningTotalRuns:
          typeof awayR1 === "number" && typeof homeR1 === "number"
            ? awayR1 + homeR1
            : null,
        firstInningAwayRuns: typeof awayR1 === "number" ? awayR1 : null,
        firstInningHomeRuns: typeof homeR1 === "number" ? homeR1 : null,
      });
    }
  }

  return NextResponse.json(
    {
      date,
      fetchedAt: new Date().toISOString(),
      games,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
