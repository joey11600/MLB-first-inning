import { loadV3Roi } from "@/lib/roi";
import { HistoryView } from "@/components/HistoryView";

export const dynamic = "force-dynamic";

/**
 * /history/v3 -- T3.24 v3 (Variant K) shadow history.
 *
 * Same UI as /history, but populated from pick_variants WHERE
 * variant_name='K' so the operator can review the v3 calibrator's
 * historical performance as if it had been the production model.
 *
 * v2 (production) remains the source of truth for real bookkeeping
 * and Telegram alerts; this page is purely informational so the
 * operator can compare model variants side-by-side without confusing
 * them.  HistoryView renders an accent-cyan banner + title badge to
 * make the model identity unambiguous.
 */
export default async function HistoryV3Page() {
  const seasonRoi = await loadV3Roi("season");
  return <HistoryView initial={seasonRoi} model="v3" />;
}
