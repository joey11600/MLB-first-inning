import { loadBoard } from "@/lib/board";
import { DashboardShell } from "@/components/DashboardShell";

export const dynamic = "force-dynamic";

export default async function HomePage({
  searchParams,
}: {
  searchParams: { date?: string };
}) {
  const initial = await loadBoard(searchParams?.date ?? null);
  return <DashboardShell initial={initial} />;
}
