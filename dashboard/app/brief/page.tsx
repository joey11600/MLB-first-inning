/**
 * /brief — ONE PICK, EXPLAINED.
 *
 * A third surface with a third usage scene. The board answers "what do I
 * bet and how much" in thirty seconds on a phone. The history page
 * answers "how has this gone" at a desk. This one answers a question the
 * operator started asking on 2026-08-03: *why is this the play*, phrased
 * so it can be read into a camera to an audience who cannot see the
 * screen.
 *
 * That scene forces different decisions from the board's. Nothing here is
 * abbreviated, because abbreviations cannot be spoken. Every figure is
 * rendered in the form it would be said aloud ("2 of their last 10", not
 * "20.0%"). The page is ordered as a script rather than as a dashboard:
 * the play, the case for it, the case against it, then the supporting
 * detail. Density is fine here in a way it is not on the board, because
 * this is a lean-in surface with no clock on it.
 *
 * EVERY PICK GETS ONE, NOT JUST THE #1 (2026-08-03). Operator: "can we
 * make an individual brief for every single pick of the day, including
 * leans. skip passes." So the page briefs any STRONG or LEAN row, and
 * the expanded row on the board links straight to it. `?game=<gamePk>`
 * chooses the game and `?date=` the slate; bare `/brief` still resolves
 * to the night's #1 through the shared selector, so the badge on the
 * board and the headline here cannot disagree.
 *
 * WHY LEAN IS IN AND PASS IS OUT — this is the whole rule, and it is not
 * arbitrary. A LEAN is a verdict: the model committed to a side, the
 * ledger records it, and it grades. It simply never gets money, because
 * tracker's Phase 1.3 rule makes LEAN track-only. There is something
 * true to explain, so it earns a page — one that says on its face that
 * no stake is going down. A PASS is the model declining to have an
 * opinion at all, and a page arguing a case for a game the model refused
 * to call would be inventing the case. That is the one thing this
 * surface exists to prevent.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { loadBoard } from "@/lib/board";
import { selectTopPick } from "@/lib/top-pick-rank";
import { loadGameFiForm } from "@/lib/first-inning-form";
import { buildReasons, type ModelDriver } from "@/lib/pick-reasons";
import { loadTopPickReport } from "@/lib/top-pick";
import { stakeUnitsFor } from "@/lib/kelly-sim";
import { briefVerdictOf, type BriefVerdict } from "@/lib/classify";
import { BriefView, type BriefOtherPlay } from "@/components/BriefView";
import type { BoardRow, GameDetail } from "@/lib/types";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

/** Same two-candidate resolution the other server readers use: the copy a
 *  built deployment ships, then the repo root when running from source. */
async function readJson<T>(...rel: string[]): Promise<T | null> {
  for (const base of [
    path.resolve(process.cwd(), "data"),
    path.resolve(process.cwd(), "..", "data"),
  ]) {
    try {
      return JSON.parse(await fs.readFile(path.join(base, ...rel), "utf8")) as T;
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

/**
 * The model's own feature contributions for this game, when they exist.
 *
 * Only the last seven days ship with the bundle (see copy-data.mjs), so
 * this is null for older slates. That is a degradation, not a failure:
 * without it the reasons fall back to editorial order and every FIGURE
 * on the page is unchanged, because the figures come from the ledger.
 */
async function loadDrivers(
  date: string,
  gamePk: string,
): Promise<ModelDriver[] | null> {
  const file = await readJson<{
    picks?: {
      game_pk?: string | number;
      top_drivers_t1?: ModelDriver[];
      top_drivers_b1?: ModelDriver[];
    }[];
  }>("diagnostics", "picks", `${date}.json`);
  const hit = file?.picks?.find((p) => String(p.game_pk) === String(gamePk));
  if (!hit) return null;
  return [...(hit.top_drivers_t1 ?? []), ...(hit.top_drivers_b1 ?? [])];
}

/* The briefable rule lives in lib/classify.ts as `briefVerdictOf`, shared
   with the board's BriefLink so the button and the page cannot disagree
   about which games have one. See that file for why LINEUP PENDING rows
   are in and STARTER PENDING rows are out. */

/** The season the slate belongs to, not the season the server is having.
 *  A brief opened for an old slate must read the ledger for THAT year or
 *  every figure on the page silently falls back to empty. */
function seasonOf(iso: string): number {
  const y = Number.parseInt((iso || "").slice(0, 4), 10);
  return Number.isFinite(y) && y > 2000 ? y : new Date().getUTCFullYear();
}

export default async function BriefPage({
  searchParams,
}: {
  searchParams: { game?: string; date?: string };
}) {
  const requestedDate = (searchParams?.date || "").trim() || null;
  const board = await loadBoard(requestedDate);
  const season = seasonOf(board.date);

  const detailFor = (r: BoardRow): GameDetail | undefined =>
    (r.gamePk ? board.details[r.gamePk] : undefined) ||
    board.details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
    board.details[`${r.away}@${r.home}`];

  /** The price on ONE named side. The side is passed in rather than read
   *  off the row because a lineup-pending row's `pickSide` is "PASS" —
   *  reading it would quote the NRFI price on a YRFI lean. */
  const oddsOn = (r: BoardRow, side: "NRFI" | "YRFI"): number | null => {
    const d = detailFor(r);
    const s = side === "YRFI" ? d?.marketYrfiOdds : d?.marketNrfiOdds;
    const n = s ? Number.parseFloat(String(s)) : NaN;
    return Number.isFinite(n) && n !== 0 ? n : null;
  };

  /* THE SLATE'S #1, BY THE ONE SHARED RULE.
     STRONG only, exactly as BoardTable filters for the badge: a LEAN is
     never wagered, so calling one "the top bet" would point at money the
     system does not intend to risk. Resolved before the requested game
     so that a brief for any OTHER pick still knows which one is #1 and
     can decline to wear the label. */
  const top = selectTopPick(
    board.rows
      .filter((r) => r.pickStrength === "STRONG")
      .map((r) => ({
        key: r,
        name: `${r.away}@${r.home}`,
        side: r.pickSide,
        modelP: r.nrfiPct / 100,
        odds: oddsOn(r, r.pickSide === "NRFI" ? "NRFI" : "YRFI"),
      })),
  );
  const topRow = top?.key ?? null;

  /** The shared rule, with this row's own run projection when there is
   *  one. Memoised per row so the fold below and the render agree. */
  const verdictOf = (r: BoardRow): BriefVerdict | null =>
    briefVerdictOf(r, board.thresholds, detailFor(r)?.lambdaLrTotal ?? null);

  /* WHICH GAME THIS PAGE IS ABOUT.
     A named game that is not briefable does NOT fall through to the #1.
     Silently swapping the game under a link the operator followed is the
     failure this whole surface is built against — they would film the
     wrong matchup. Each dead end gets its own honest state instead. */
  let row: BoardRow | null = null;
  let verdict: BriefVerdict | null = null;
  let empty: { kind: "none" | "pass" | "missing"; away?: string; home?: string } | undefined;
  const wanted = (searchParams?.game || "").trim();
  if (wanted) {
    const hit = board.rows.find((r) => r.gamePk === wanted) ?? null;
    const v = hit ? verdictOf(hit) : null;
    if (hit && v) { row = hit; verdict = v; }
    else if (hit) empty = { kind: "pass", away: hit.away, home: hit.home };
    else empty = { kind: "missing" };
  } else {
    row = topRow;
    verdict = row ? verdictOf(row) : null;
    if (!row) empty = { kind: "none" };
  }

  const topPick = await loadTopPickReport(season).catch(() => null);

  /* Every other pick on the slate, as a way out of any state this page
     can land in — including the dead ends above, which is why the list
     is built before the early return rather than inside the happy path.
     Ordered #1, then the rest of the bets, then the leans: that is the
     order the operator works through them. */
  const otherPlays: BriefOtherPlay[] = board.rows
    .map((r) => ({ r, v: verdictOf(r) }))
    .filter(
      (x): x is { r: BoardRow; v: BriefVerdict } =>
        x.v != null && Boolean(x.r.gamePk) && x.r.gamePk !== (row?.gamePk ?? ""),
    )
    .map(({ r, v }): BriefOtherPlay => ({
      gamePk: r.gamePk,
      away: r.away,
      home: r.home,
      side: v.side,
      strength: v.strength,
      pending: v.pending,
      isTop: topRow != null && r.gamePk === topRow.gamePk,
      gameTimeEt: r.gameTimeEt,
    }))
    /* #1, then the settled picks, then everything still waiting on a
       lineup. That is the order the operator works through them: what is
       decided first, what might still move last. */
    .sort((a, b) => {
      if (a.isTop !== b.isTop) return a.isTop ? -1 : 1;
      if (a.pending !== b.pending) return a.pending ? 1 : -1;
      if (a.strength !== b.strength) return a.strength === "STRONG" ? -1 : 1;
      return (a.gameTimeEt || "").localeCompare(b.gameTimeEt || "");
    });

  const slateDate = requestedDate ?? undefined;

  if (!row) {
    return (
      <BriefView
        date={board.date}
        slateDate={slateDate}
        play={null}
        isTop={false}
        reasons={null}
        form={null}
        record={topPick}
        otherPlays={otherPlays}
        empty={empty ?? { kind: "none" }}
      />
    );
  }

  const detail = detailFor(row);
  /* THE SIDE COMES FROM THE VERDICT, NOT THE ROW. On a lineup-pending
     row `row.pickSide` is literally "PASS" — the ledger has not committed
     yet — while the verdict carries the side the model is actually
     leaning. Reading the row here would brief the wrong half of the
     inning and every reason on the page would be scored against it. */
  const side = verdict!.side;
  const odds = oddsOn(row, side);
  const modelP = (side === "NRFI" ? row.nrfiPct : row.yrfiPct) / 100;
  const noStake = verdict!.pending || verdict!.strength === "LEAN";

  const parkFactors = await readJson<Record<string, number>>(
    "fi_park_factors.json",
  );

  const form = await loadGameFiForm({
    season,
    date: board.date,
    away: row.away,
    home: row.home,
    awayPitcherId: detail?.away.pitcher.mlbId
      ? String(detail.away.pitcher.mlbId)
      : null,
    homePitcherId: detail?.home.pitcher.mlbId
      ? String(detail.home.pitcher.mlbId)
      : null,
    awayPitcherName: detail?.away.pitcher.name ?? null,
    homePitcherName: detail?.home.pitcher.name ?? null,
    parkFactors,
  });

  const drivers = row.gamePk
    ? await loadDrivers(board.date, row.gamePk).catch(() => null)
    : null;

  const reasons = form ? buildReasons(form, side, drivers) : null;

  return (
    <BriefView
      date={board.date}
      slateDate={slateDate}
      play={{
        away: row.away,
        home: row.home,
        gamePk: row.gamePk,
        gameTimeEt: row.gameTimeEt,
        side,
        modelP,
        odds,
        /* NO STAKE ON A LEAN OR A PENDING PICK, EVER — not even a
           computed one. `stakeUnitsFor` will happily quarter-Kelly
           either, and the figure would be plausible, printed at 20px in
           the ticket, and read aloud. On a lean it would be an
           instruction to risk money on a pick the tracker marks
           bet_placed='N' by rule; on a pending pick it would be a stake
           for a bet that has not been decided and can still flip when
           the lineup posts. Same guard the board's stake chip runs. */
        stake: !noStake && odds != null ? stakeUnitsFor(modelP, odds) : null,
        awayPitcher: detail?.away.pitcher.name ?? null,
        homePitcher: detail?.home.pitcher.name ?? null,
        strength: verdict!.strength,
        pending: verdict!.pending,
      }}
      isTop={topRow != null && row.gamePk !== "" && row.gamePk === topRow.gamePk}
      reasons={reasons}
      form={form}
      record={topPick}
      otherPlays={otherPlays}
    />
  );
}
