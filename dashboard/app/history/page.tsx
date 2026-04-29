import { loadRoi } from "@/lib/roi";
import { HistoryView } from "@/components/HistoryView";

export const dynamic = "force-dynamic";

export default async function HistoryPage() {
  // Pull the season window directly so the page renders SSR-fresh.
  const seasonRoi = await loadRoi("season");
  return <HistoryView initial={seasonRoi} />;
}
