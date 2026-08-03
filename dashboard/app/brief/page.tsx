/**
 * /brief — THE #1 PLAY, EXPLAINED.
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
 * `?game=<gamePk>` briefs any game on the slate. Bare `/brief` picks the
 * night's #1 using the shared selector, so the badge on the board and the
 * headline here cannot disagree.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { loadBoard } from "@/lib/board";
import { selectTopPick } from "@/lib/top-pick-rank";
import { loadGameFiForm } from "@/lib/first-inning-form";
import { buildReasons, type ModelDriver } from "@/lib/pick-reasons";
import { loadTopPickReport } from "@/lib/top-pick";
import { stakeUnitsFor } from "@/lib/kelly-sim";
import { BriefView } from "@/components/BriefView";
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

export default async function BriefPage({
  searchParams,
}: {
  searchParams: { game?: string };
}) {
  const board = await loadBoard(null);

  const detailFor = (r: BoardRow): GameDetail | undefined =>
    board.details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
    board.details[`${r.away}@${r.home}`];

  const oddsOn = (r: BoardRow): number | null => {
    const d = detailFor(r);
    const s = r.pickSide === "YRFI" ? d?.marketYrfiOdds : d?.marketNrfiOdds;
    const n = s ? Number.parseFloat(String(s)) : NaN;
    return Number.isFinite(n) && n !== 0 ? n : null;
  };

  // The requested game, else the slate's #1 by the shared rule.
  let row: BoardRow | null = null;
  if (searchParams?.game) {
    row = board.rows.find((r) => r.gamePk === searchParams.game) ?? null;
  }
  if (!row) {
    const best = selectTopPick(
      board.rows
        .filter((r) => r.pickStrength === "STRONG")
        .map((r) => ({
          key: r,
          name: `${r.away}@${r.home}`,
          side: r.pickSide,
          modelP: r.nrfiPct / 100,
          odds: oddsOn(r),
        })),
    );
    row = best?.key ?? null;
  }

  const topPick = await loadTopPickReport(new Date().getUTCFullYear()).catch(
    () => null,
  );

  if (!row) {
    return (
      <BriefView
        date={board.date}
        play={null}
        reasons={null}
        form={null}
        record={topPick}
        otherPlays={[]}
      />
    );
  }

  const detail = detailFor(row);
  const side = row.pickSide === "NRFI" ? "NRFI" : "YRFI";
  const odds = oddsOn(row);
  const modelP = (side === "NRFI" ? row.nrfiPct : row.yrfiPct) / 100;

  const parkFactors = await readJson<Record<string, number>>(
    "fi_park_factors.json",
  );

  const form = await loadGameFiForm({
    season: new Date().getUTCFullYear(),
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
      play={{
        away: row.away,
        home: row.home,
        gamePk: row.gamePk,
        gameTimeEt: row.gameTimeEt,
        side,
        modelP,
        odds,
        stake: odds != null ? stakeUnitsFor(modelP, odds) : null,
        awayPitcher: detail?.away.pitcher.name ?? null,
        homePitcher: detail?.home.pitcher.name ?? null,
        strength: row.pickStrength,
      }}
      reasons={reasons}
      form={form}
      record={topPick}
      otherPlays={board.rows
        .filter(
          (r) =>
            r.pickStrength === "STRONG" &&
            r.gamePk &&
            r.gamePk !== row!.gamePk,
        )
        .map((r) => ({
          gamePk: r.gamePk,
          away: r.away,
          home: r.home,
          side: r.pickSide === "NRFI" ? "NRFI" : "YRFI",
        }))}
    />
  );
}
