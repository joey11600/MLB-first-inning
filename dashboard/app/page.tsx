import { loadBoard } from "@/lib/board";
import { DashboardShell } from "@/components/DashboardShell";

export const dynamic = "force-dynamic";
// T2.35: disable Next.js fetch cache for this page.
//
// Phase 2's Supabase reader (lib/board-supabase.ts) issues HTTP requests
// to PostgREST under the hood.  Next.js 14 wraps the global fetch with
// its data-cache layer; by default fetches inside server components are
// memoized FOR THE LIFETIME OF THE BUILD, even when the page itself is
// `dynamic = "force-dynamic"`.  The result was that the SSR snapshot of
// /'s loadBoard() served a stale row set from the moment of the
// Vercel build, while /api/board (which is a Route Handler with its
// own `revalidate = 0`) served fresh rows from Supabase.
//
// `fetchCache = "force-no-store"` opts the page out of fetch caching
// entirely so every render re-queries Supabase.  Same effect as
// putting `cache: 'no-store'` on every individual fetch, but applied
// page-wide so Supabase's internal fetches inherit it.
export const fetchCache = "force-no-store";

export default async function HomePage() {
  // T-V21-2026-05-06: removed `?date=YYYY-MM-DD` query param handling.
  // The URL serialization caused stale-bookmark stickiness: viewing a
  // past date once put `?date=2026-05-02` in the URL, the user
  // bookmarked / kept that tab, and every subsequent visit re-loaded
  // 5/02 instead of today's slate.
  //
  // Now: every initial page load fetches the latest available slate.
  // The date picker in ControlPanel still navigates to historical
  // dates (client-side state via refetch); just doesn't sync to the
  // URL.  Refresh always returns the user to today.
  const initial = await loadBoard(null);
  return <DashboardShell initial={initial} />;
}
