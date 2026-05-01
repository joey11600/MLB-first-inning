import { loadBoard } from "@/lib/board";
import { DashboardShell } from "@/components/DashboardShell";

export const dynamic = "force-dynamic";

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
