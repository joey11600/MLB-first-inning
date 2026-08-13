import { getServerSupabase } from "@/lib/supabase";
import { CardsView, type CardItem } from "@/components/CardsView";

export const dynamic = "force-dynamic";

/**
 * /cards — the Backfist Bets social cards, on whatever device is to hand.
 *
 * WHY A BUCKET AND NOT A RENDER ENDPOINT. The cards are drawn by
 * `tools/cards/make_card.py` (Pillow) and uploaded to the public `cards`
 * bucket. Re-implementing that renderer here in Satori/next-og would put the
 * SAME design in two languages, and the first time one side changed the
 * published image and the dashboard preview would disagree — the exact drift
 * this project keeps having to fix elsewhere (see the top-pick rule, which has
 * to be mirrored in three places and says so in every one of them).
 *
 * So there is one renderer, and this page is a viewer.
 *
 * Reads with the anon key. The bucket is public-read and a storage.objects
 * SELECT policy scoped to `bucket_id = 'cards'` allows the listing; uploads
 * still require SUPABASE_SERVICE_KEY.
 */
function parse(name: string): { date: string; plate: string } | null {
  // backfist_2026-08-12_leather.png
  const m = /^backfist_(\d{4}-\d{2}-\d{2})_([a-z0-9-]+)\.png$/i.exec(name);
  return m ? { date: m[1], plate: m[2] } : null;
}

async function loadCards(): Promise<{ items: CardItem[]; configured: boolean }> {
  const sb = getServerSupabase();
  if (!sb) return { items: [], configured: false };

  const { data, error } = await sb.storage
    .from("cards")
    .list("", { limit: 400, sortBy: { column: "name", order: "desc" } });
  if (error || !data) return { items: [], configured: true };

  const items: CardItem[] = [];
  for (const obj of data) {
    const p = parse(obj.name);
    if (!p) continue;                       // ignore anything not a card
    items.push({
      name: obj.name,
      date: p.date,
      plate: p.plate,
      url: sb.storage.from("cards").getPublicUrl(obj.name).data.publicUrl,
      bytes: (obj.metadata as { size?: number } | null)?.size ?? 0,
    });
  }
  // Newest night first; within a night, keep the plate order stable.
  items.sort((a, b) => (a.date === b.date
    ? a.plate.localeCompare(b.plate)
    : b.date.localeCompare(a.date)));
  return { items, configured: true };
}

export default async function CardsPage() {
  const { items, configured } = await loadCards();
  return <CardsView items={items} configured={configured} />;
}
