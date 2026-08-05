import { redirect } from "next/navigation";
import { loadBoard } from "@/lib/board";
import { loadTopPickReport } from "@/lib/top-pick";
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

export default async function HomePage({
  searchParams,
}: {
  searchParams: { date?: string };
}) {
  // T-V21-2026-05-06: any `?date=` query param triggers a real HTTP
  // redirect to "/".  Reasons it's a HARD redirect, not just ignoring
  // the param:
  //
  //   1. Browser cache.  The HTML at "/?date=2026-05-02" was cached
  //      from a past visit (when that URL legitimately served 5/02
  //      data).  A normal Cache-Control: no-store header doesn't
  //      bust BFCache (Chrome's back/forward snapshot cache) so the
  //      browser keeps showing the stale page when the user hits
  //      "back" from /history.  A 307 redirect IS respected by
  //      BFCache: the browser sees the redirect response and
  //      replaces the URL.
  //
  //   2. Bookmark hygiene.  If the user bookmarked "/?date=X",
  //      visiting that bookmark redirects to "/" -- the bookmark
  //      effectively self-heals.
  //
  //   3. Symmetry with the client-side strip in DashboardShell.
  //      Both server (here) and client (mount useEffect) refuse to
  //      keep `?date=` in the URL, so the bug can't sneak back in.
  if (searchParams?.date) {
    redirect("/");
  }
  /* The #1 system's real-money report rides along for the front-page
     hero's credentials row -- the SAME loader /history's lead section
     uses, so the two surfaces cannot quote different records.
     Soft-fails to null: a missing ledger costs the credentials row,
     never the page. */
  const [initial, topPick] = await Promise.all([
    loadBoard(null),
    loadTopPickReport(new Date().getUTCFullYear()).catch(() => null),
  ]);
  return <DashboardShell initial={initial} topPick={topPick} />;
}
