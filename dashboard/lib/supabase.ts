/**
 * dashboard/lib/supabase.ts — Supabase client for the dashboard.
 *
 * Phase 2 of the real-time architecture migration.  This module exposes
 * thin client factories used by:
 *   - server-side API routes (lib/board-supabase.ts) that read picks
 *     from the public.picks_2026 table at request time
 *   - client-side React components (lib/useSupabaseRealtime.ts) that
 *     subscribe to row-change events on picks_2026 / pick_changes /
 *     live_game_state to push instant updates without polling
 *
 * Both use the anon key.  Read-only access is enforced by RLS policies
 * in db/schema.sql -- anon and authenticated roles can SELECT but not
 * INSERT / UPDATE / DELETE.  Writes happen exclusively through
 * tracker.py via SUPABASE_SERVICE_KEY (which bypasses RLS).
 *
 * If the env vars are missing the factory returns null so the caller
 * can gracefully fall back to the CSV read path.  This keeps local
 * dev / preview environments working before secrets land.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// ---------------------------------------------------------------------------
// Env vars
// ---------------------------------------------------------------------------
//
// NEXT_PUBLIC_* vars are inlined into the client bundle at build time, so
// these can be read on the browser AND in Node.  We deliberately don't
// expose SUPABASE_SERVICE_KEY here — that lives only in the predictor cron
// (GHA secret) and never touches the dashboard.

function readEnv(): { url: string; anonKey: string } | null {
  // Resolve from process.env at call time rather than module init so
  // tests / Next.js dev mode can mutate env vars between requests
  // without the singleton caching stale values.
  const url     = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim()      ?? "";
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim() ?? "";
  if (!url || !anonKey) return null;
  return { url, anonKey };
}


// ---------------------------------------------------------------------------
// Server-side client (API routes)
// ---------------------------------------------------------------------------
//
// API routes are stateless per-request, so we don't strictly need a
// singleton -- but creating a fresh client on every request would
// instantiate a Realtime WebSocket too, which is wasteful.  Disable
// Realtime + auth refresh on the server-side variant since we only
// use it for `from(...).select()` queries.

let _serverClient: SupabaseClient | null = null;

export function getServerSupabase(): SupabaseClient | null {
  if (_serverClient) return _serverClient;
  const env = readEnv();
  if (!env) return null;
  _serverClient = createClient(env.url, env.anonKey, {
    auth: {
      // No user sessions on the server-side path -- keeps overhead low.
      persistSession:   false,
      autoRefreshToken: false,
    },
    realtime: {
      // Server doesn't need Realtime -- our subscriptions live in the
      // browser.  Opting out of channel setup saves an extra WebSocket
      // per cold start.
      params: { eventsPerSecond: 0 },
    },
    global: {
      // Identify ourselves in Supabase logs so we can tell dashboard
      // traffic apart from tracker.py + the migration script.
      headers: { "x-client-info": "nrfi-dashboard-server" },
      // T2.35: force `cache: "no-store"` on every Supabase request so
      // Next.js's data cache (which wraps the global fetch in server
      // components) doesn't memoize PostgREST responses for the
      // build's lifetime.  Without this, /api/board (Route Handler
      // with explicit revalidate=0) served fresh data while / (page
      // SSR) served stale data from the Vercel build moment.  We
      // never want a cached Supabase row set on the dashboard --
      // staleness is exactly what Realtime is supposed to fix.
      fetch: (input: RequestInfo | URL, init?: RequestInit) =>
        fetch(input, { ...init, cache: "no-store" }),
    },
  });
  return _serverClient;
}


// ---------------------------------------------------------------------------
// Browser client (Realtime subscriptions, client-side fetches)
// ---------------------------------------------------------------------------
//
// The browser singleton MUST live across React re-renders, otherwise
// every render would create a new WebSocket and the Realtime subscription
// state would churn.  Stash on globalThis so HMR + Strict Mode don't
// double-instantiate during development.

declare global {
  // eslint-disable-next-line no-var
  var __nrfi_supabase_browser_client: SupabaseClient | undefined;
}

export function getBrowserSupabase(): SupabaseClient | null {
  if (typeof window === "undefined") {
    // Defensive: if someone calls this from server code by mistake,
    // fall back to the server client rather than throwing.
    return getServerSupabase();
  }
  if (globalThis.__nrfi_supabase_browser_client) {
    return globalThis.__nrfi_supabase_browser_client;
  }
  const env = readEnv();
  if (!env) return null;
  globalThis.__nrfi_supabase_browser_client = createClient(env.url, env.anonKey, {
    auth: { persistSession: true, autoRefreshToken: true },
    global: {
      headers: { "x-client-info": "nrfi-dashboard-browser" },
    },
  });
  return globalThis.__nrfi_supabase_browser_client;
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** True when the dashboard is configured to read from Supabase.  Lets
 *  callers (loadBoard, useSupabaseRealtime) decide whether to attempt
 *  Supabase or skip straight to the CSV/no-op path without paying the
 *  client-init cost.  Never throws. */
export function isSupabaseConfigured(): boolean {
  return readEnv() !== null;
}
