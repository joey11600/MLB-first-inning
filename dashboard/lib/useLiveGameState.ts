"use client";

import { useEffect, useRef, useState } from "react";

export interface LiveGameState {
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

export interface LiveStateResponse {
  date: string;
  fetchedAt: string;
  games: LiveGameState[];
}

/**
 * Hook: poll /api/live-state every N seconds (default 30s) and return the
 * latest map of gamePk -> live game state.  Pauses when tab is hidden.
 *
 * Usage:
 *   const { byGamePk, byTeam, fetchedAt } = useLiveGameState("2026-05-01");
 *   const live = byGamePk[row.gamePk] ?? byTeam[`${row.away}@${row.home}`];
 *   if (live?.abstractGameState === "Live") { ... }
 *
 * Polls only when the date is current-or-future (no point re-polling
 * yesterday's slate -- those games are over).  Stops automatically once
 * all games on the slate are Final.
 */
export function useLiveGameState(date: string | null, intervalMs: number = 30_000) {
  const [state, setState] = useState<LiveStateResponse | null>(null);
  const dateRef = useRef(date);
  useEffect(() => { dateRef.current = date; }, [date]);

  useEffect(() => {
    if (!date) return;

    // Only poll for today or future dates.  Historical slates don't change.
    const todayIso = new Date().toISOString().slice(0, 10);
    if (date < todayIso) return;

    let cancelled = false;
    let timer: number | null = null;

    async function fetchOnce() {
      const d = dateRef.current;
      if (!d) return;
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      try {
        const res = await fetch(`/api/live-state?date=${encodeURIComponent(d)}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const json = (await res.json()) as LiveStateResponse;
        if (!cancelled) setState(json);
      } catch {
        // swallow network errors -- the next poll will retry
      }
    }

    // Initial fetch immediately, then on interval
    void fetchOnce();
    timer = window.setInterval(fetchOnce, intervalMs);

    // Re-fetch on tab focus / visibility change so coming back to the tab
    // shows fresh state instantly without waiting for the next poll tick.
    const onVisible = () => {
      if (document.visibilityState === "visible") void fetchOnce();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [date, intervalMs]);

  // Build lookup tables once per state update for cheap row-level access.
  const byGamePk: Record<string, LiveGameState> = {};
  const byTeam:   Record<string, LiveGameState> = {};
  for (const g of state?.games ?? []) {
    if (g.gamePk) byGamePk[String(g.gamePk)] = g;
    if (g.away && g.home) byTeam[`${g.away}@${g.home}`] = g;
  }

  return {
    byGamePk,
    byTeam,
    fetchedAt: state?.fetchedAt ?? null,
    raw: state,
  };
}
