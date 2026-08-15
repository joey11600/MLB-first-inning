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

/** backfist_2026-08-12_post.txt — the ready-to-paste X post for that night.
 *  A .txt cannot match `parse()` above (it anchors on `.png`), so the two
 *  object kinds sort themselves out and neither sees the other's files. */
const POST = /^backfist_(\d{4}-\d{2}-\d{2})_post\.txt$/i;

async function loadCards(): Promise<{
  items: CardItem[]; posts: Record<string, string>; configured: boolean;
}> {
  const sb = getServerSupabase();
  if (!sb) return { items: [], posts: {}, configured: false };

  const { data, error } = await sb.storage
    .from("cards")
    .list("", { limit: 400, sortBy: { column: "name", order: "desc" } });
  if (error || !data) return { items: [], posts: {}, configured: true };

  const items: CardItem[] = [];
  const postObjs: { date: string; url: string }[] = [];
  for (const obj of data) {
    const pm = POST.exec(obj.name);
    if (pm) {
      postObjs.push({
        date: pm[1],
        url: sb.storage.from("cards").getPublicUrl(obj.name).data.publicUrl,
      });
      continue;
    }
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

  // Fetched here rather than in the browser so the post is in the HTML on
  // first paint — the operator opens this page to copy one thing, and a
  // client-side round trip would put a spinner between them and it.
  // `no-store` because a re-render upserts the same object name: a cached
  // body would show a stale post beside a fresh card.
  const posts: Record<string, string> = {};
  await Promise.all(postObjs.map(async ({ date, url }) => {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (r.ok) {
        const t = (await r.text()).trim();
        if (t) posts[date] = t;
      }
    } catch {
      // A missing post costs the night its Copy block, nothing else.
    }
  }));

  // Newest night first; within a night, keep the plate order stable.
  items.sort((a, b) => (a.date === b.date
    ? a.plate.localeCompare(b.plate)
    : b.date.localeCompare(a.date)));
  return { items, posts, configured: true };
}

export default async function CardsPage() {
  const { items, posts, configured } = await loadCards();
  return <CardsView items={items} posts={posts} configured={configured} />;
}
