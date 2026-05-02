"use client";

import { useEffect, useRef, useState } from "react";
import { getBrowserSupabase, isSupabaseConfigured } from "./supabase";
import type { RealtimeChannel, SupabaseClient } from "@supabase/supabase-js";

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
 * Hook returning the latest map of gamePk -> live game state for a slate.
 *
 * Two paths, picked at runtime:
 *
 *   A. Supabase Realtime (Phase 4 — preferred when env vars are set):
 *      - Initial SELECT from public.live_game_state
 *      - Subscribe to postgres_changes (INSERT|UPDATE on rows where
 *        date matches the slate)
 *      - Each event merges into local state in O(1) — no extra fetches
 *      - Fed by the Railway live-state worker pushing every ~10 sec
 *      - End-to-end latency: worker push -> dashboard render is ~200ms
 *
 *   B. /api/live-state polling (back-compat):
 *      - Original 30-second polling against the Next.js API route
 *      - Used when NEXT_PUBLIC_SUPABASE_* env vars are missing or for
 *        any user on a deploy without the worker fed (preview / older
 *        production builds)
 *
 * The hook auto-skips for past dates in both modes (no live state to
 * track on yesterday's slate) and clamps the polling fallback to the
 * tab-visible state so background tabs don't burn battery.
 *
 * Usage (unchanged across modes):
 *   const { byGamePk, byTeam, fetchedAt } = useLiveGameState("2026-05-02");
 *   const live = byGamePk[row.gamePk] ?? byTeam[`${row.away}@${row.home}`];
 *   if (live?.abstractGameState === "Live") { ... }
 */
export function useLiveGameState(date: string | null, intervalMs: number = 30_000) {
  const [state, setState] = useState<LiveStateResponse | null>(null);
  const dateRef = useRef(date);
  useEffect(() => { dateRef.current = date; }, [date]);

  // Branch A: Supabase Realtime (push-based, sub-second).
  useEffect(() => {
    if (!date) return;
    const todayIso = new Date().toISOString().slice(0, 10);
    if (date < todayIso) return;
    if (!isSupabaseConfigured()) return;

    const sb = getBrowserSupabase();
    if (!sb) return;

    let cancelled = false;

    // Initial SELECT — gives us the current snapshot before any
    // subsequent Realtime event lands.  Without this, the dashboard
    // would have NO data until the next worker write fires (could be
    // up to 10s during games, 5min during quiet periods).
    void primeFromSupabase(sb, date).then((games) => {
      if (cancelled) return;
      setState({
        date,
        fetchedAt: new Date().toISOString(),
        games,
      });
    });

    // Subscribe to row changes for THIS slate's date.  Filter at
    // Postgres level so we don't burn bandwidth on other dates' rows
    // (free Realtime tier has a per-second message budget).
    const channel: RealtimeChannel = sb
      .channel(`live-game-state-${date}`)
      .on(
        "postgres_changes",
        {
          event:  "*",
          schema: "public",
          table:  "live_game_state",
          filter: `date=eq.${date}`,
        },
        (payload) => {
          if (cancelled) return;
          // Merge the new row into our state.  We do it inside
          // setState so React batches concurrent events naturally.
          setState((prev) => {
            const next: LiveGameState | null = rowToLiveGame(payload.new as Record<string, unknown>);
            const oldNext: LiveGameState | null = rowToLiveGame(payload.old as Record<string, unknown>);
            const games = (prev?.games ?? []).slice();
            // Match by game_pk -- INSERT and UPDATE both carry the
            // post-state in `payload.new`.
            const targetPk = next?.gamePk ?? oldNext?.gamePk;
            if (targetPk == null) return prev;
            const idx = games.findIndex((g) => g.gamePk === targetPk);
            if (payload.eventType === "DELETE") {
              if (idx >= 0) games.splice(idx, 1);
            } else if (next) {
              if (idx >= 0) games[idx] = next;
              else games.push(next);
            }
            return {
              date,
              fetchedAt: new Date().toISOString(),
              games,
            };
          });
        },
      );

    channel.subscribe((status) => {
      if (status !== "SUBSCRIBED") {
        // Don't spam logs on the happy path; surface any unusual states
        // (CHANNEL_ERROR / TIMED_OUT / CLOSED) for debugging.
        console.info(`[live-state realtime] channel: ${status}`);
      }
    });

    return () => {
      cancelled = true;
      sb.removeChannel(channel);
    };
  }, [date]);

  // Branch B: /api/live-state polling fallback (used when Supabase
  // env vars are missing).  Only mounts when Supabase is unconfigured
  // — the early-return below makes this an inert effect when the
  // Realtime path is active.
  useEffect(() => {
    if (!date) return;
    if (isSupabaseConfigured()) return;   // Realtime path handles it
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
        // swallow network errors; the next poll retries
      }
    }

    void fetchOnce();
    timer = window.setInterval(fetchOnce, intervalMs);

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

  // Lookup-table derivation -- O(n) per render, n ~= 15.
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


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Initial fetch — SELECT all of today's live_game_state rows so the
 *  dashboard renders with current state immediately, instead of an
 *  empty map until the first Realtime event fires. */
async function primeFromSupabase(sb: SupabaseClient, date: string): Promise<LiveGameState[]> {
  try {
    const { data, error } = await sb
      .from("live_game_state")
      .select("*")
      .eq("date", date)
      .order("away_team", { ascending: true });
    if (error || !data) return [];
    return data
      .map((r) => rowToLiveGame(r as Record<string, unknown>))
      .filter((g): g is LiveGameState => g !== null);
  } catch {
    return [];
  }
}


/** Map a public.live_game_state row (Postgres column shape) to the
 *  client-side LiveGameState (camelCase) used by every consumer.  Mirrors
 *  the field shape produced by /api/live-state so callers can swap data
 *  sources without behavior change. */
function rowToLiveGame(r: Record<string, unknown> | null | undefined): LiveGameState | null {
  if (!r || typeof r !== "object") return null;
  // game_pk is stored as TEXT (composite-PK convention with picks_2026);
  // coerce to number for the existing client interface.
  const gp = r.game_pk;
  const gamePk = typeof gp === "number"
    ? gp
    : typeof gp === "string"
      ? Number.parseInt(gp, 10) || 0
      : 0;
  if (!gamePk) return null;

  const num = (v: unknown): number =>
    typeof v === "number" && Number.isFinite(v) ? v : 0;
  const nullableNum = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  const str = (v: unknown): string =>
    typeof v === "string" ? v : "";

  return {
    gamePk,
    away:                 str(r.away_team),
    home:                 str(r.home_team),
    status:               str(r.status),
    abstractGameState:    str(r.abstract_game_state),
    currentInning:        nullableNum(r.current_inning),
    inningState:          str(r.inning_state),
    awayScore:            num(r.away_score),
    homeScore:            num(r.home_score),
    firstInningComplete:  Boolean(r.fi_complete),
    firstInningTotalRuns: nullableNum(r.fi_total_runs),
    firstInningAwayRuns:  nullableNum(r.fi_away_runs),
    firstInningHomeRuns:  nullableNum(r.fi_home_runs),
  };
}
