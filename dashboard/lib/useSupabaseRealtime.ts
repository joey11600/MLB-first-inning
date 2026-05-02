"use client";

/**
 * dashboard/lib/useSupabaseRealtime.ts — Phase 2 client hook.
 *
 * Subscribes to row changes on picks_<season> / pick_changes /
 * live_game_state and fires a callback whenever ANY row in those
 * tables changes.  The callback typically triggers a refetch of
 * /api/board so the dashboard re-renders with the latest data.
 *
 * Why a single callback instead of per-table state diffs:
 *   The first iteration intentionally keeps the contract minimal --
 *   "something changed, please refetch."  This is back-compat with the
 *   existing 30s polling loop in DashboardShell (it just makes the
 *   poll happen sooner).  A future iteration could merge row changes
 *   directly into client state to skip the API hop, but the simple
 *   refetch is robust against any Realtime edge case (missed events,
 *   reconnects, schema drift).
 *
 * Connection lifecycle:
 *   - Subscribe on mount; tear down on unmount.
 *   - Auto-skipped when not in the browser (SSR safe), when env vars
 *     are missing (CSV-only mode), or when `enabled = false` (e.g.
 *     viewing historical dates where realtime updates are pointless).
 */

import { useEffect, useRef } from "react";
import { getBrowserSupabase, isSupabaseConfigured } from "./supabase";
import type { RealtimeChannel } from "@supabase/supabase-js";

interface Options {
  /** ISO date currently displayed.  When the user is viewing a past
   *  date, we skip the subscription -- there's nothing to update. */
  date:    string | null;
  /** Season (used to subscribe to the correct picks_<season> table). */
  season?: number;
  /** Whether to subscribe at all.  Useful for kill-switching the
   *  feature without removing the hook from the tree. */
  enabled?: boolean;
  /** Fired whenever any row changes in the subscribed tables.  The
   *  argument tells the caller which table fired so it can be selective
   *  (e.g. don't refetch /api/board for live_game_state changes since
   *  that data lives in the separate /api/live-state endpoint). */
  onChange: (table: "picks" | "pick_changes" | "live_game_state") => void;
}

export function useSupabaseRealtime({
  date,
  season  = 2026,
  enabled = true,
  onChange,
}: Options): void {
  // Stash callback in a ref so the subscription effect doesn't tear
  // down + recreate every render just because the parent passed a new
  // callback identity (typical for inline arrow functions).
  const cbRef = useRef(onChange);
  cbRef.current = onChange;

  useEffect(() => {
    if (!enabled) return;
    if (typeof window === "undefined") return;
    if (!isSupabaseConfigured()) return;
    // Past dates don't get realtime updates — nothing's mutating.
    // We only subscribe for "today or future" so historical browsing
    // doesn't burn an idle WebSocket.
    if (!date) return;
    const todayIso = new Date().toISOString().slice(0, 10);
    if (date < todayIso) return;

    const sb = getBrowserSupabase();
    if (!sb) return;

    const picksTable = `picks_${season}`;

    // One channel per tab, three subscriptions on it.  Supabase Realtime
    // multiplexes them over a single WebSocket so this is cheap.
    const channel: RealtimeChannel = sb.channel(`nrfi-board-${date}`)
      .on(
        "postgres_changes",
        {
          event:  "*",            // INSERT | UPDATE | DELETE
          schema: "public",
          table:  picksTable,
          filter: `date=eq.${date}`,    // only the slate we're viewing
        },
        () => cbRef.current("picks"),
      )
      .on(
        "postgres_changes",
        {
          event:  "*",
          schema: "public",
          table:  "pick_changes",
          filter: `date=eq.${date}`,
        },
        () => cbRef.current("pick_changes"),
      )
      .on(
        "postgres_changes",
        {
          event:  "*",
          schema: "public",
          table:  "live_game_state",
          filter: `date=eq.${date}`,
        },
        () => cbRef.current("live_game_state"),
      );

    channel.subscribe((status) => {
      // Only log unusual states — SUBSCRIBED is the happy path and gets
      // chatty if we log it.  CHANNEL_ERROR / TIMED_OUT / CLOSED matter
      // for diagnosing dashboards that aren't getting updates.
      if (status !== "SUBSCRIBED") {
        console.info(`[realtime] channel status: ${status}`);
      }
    });

    return () => {
      sb.removeChannel(channel);
    };
  // We intentionally exclude `onChange` from deps -- the ref pattern
  // above means a new callback identity does NOT trigger a teardown.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, season, enabled]);
}
