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

/** Strict ISO YYYY-MM-DD validation -- prevents `?date=2099-12-31` or
 *  `?date=garbage` from being passed through to loadBoard, where it
 *  silently falls back to the latest available date and the URL state
 *  becomes inconsistent with what's displayed. */
function isValidIsoDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split("-").map(Number);
  if (m < 1 || m > 12 || d < 1 || d > 31) return false;
  const dt = new Date(Date.UTC(y, m - 1, d));
  return (
    dt.getUTCFullYear() === y &&
    dt.getUTCMonth() === m - 1 &&
    dt.getUTCDate() === d
  );
}

export default async function HomePage({
  searchParams,
}: {
  searchParams: { date?: string };
}) {
  const raw = (searchParams?.date ?? "").trim();
  // Pass null when the param is missing OR malformed; loadBoard will
  // fall back to the most recent available date in either case.
  const requested = raw && isValidIsoDate(raw) ? raw : null;
  const initial = await loadBoard(requested);
  return <DashboardShell initial={initial} />;
}
