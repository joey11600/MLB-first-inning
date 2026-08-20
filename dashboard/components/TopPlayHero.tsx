"use client";

/**
 * TopPlayHero -- the front page. THE №1 PLAY, as a newspaper lead story.
 *
 * 2026-08-05 redesign, operator's instruction: "i want the #1 pick
 * system to be our main system. we still track the total system record
 * and units, but the #1 pick is the main system."
 *
 * This replaces TonightsActionCard as the page lead. That card counted
 * things (flagged / placed / settled, a side split, an at-risk sum) and
 * needed a paragraph to explain its own vocabulary. The operator's
 * question is singular -- "what is THE bet tonight, and how much" -- so
 * the lead is now singular: one game, in display type, with the four
 * numbers that place the bet (side, price, stake, price limit) and the
 * #1 system's real-money record as its standing credentials.
 *
 * WHICH GAME IS #1 comes from selectTopPick -- the SAME rule the board
 * badge and /brief use (lib/top-pick-rank). This surface adds no rule
 * of its own; three surfaces disagreeing about #1 is the class of
 * contradiction this dashboard keeps being cleared of.
 *
 * PROVENANCE RULES (PRODUCT.md): the credentials row is REAL money from
 * the ledger (loadTopPickReport, passed down from the server). Stake and
 * limit are the published quarter-Kelly rule -- the number a follower on
 * any bankroll stakes. Nothing here is simulated, and no odds are ever
 * fabricated: a missing DraftKings price renders as "no price yet",
 * never as a guess.
 */

import { useMemo } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import type { TopPickReport } from "@/lib/top-pick";
import { selectTopPick } from "@/lib/top-pick-rank";
import { stakeUnitsFor } from "@/lib/kelly-sim";
import { buildPriceLadder, formatAmerican } from "@/lib/price-ladder";
import { cityOf } from "@/lib/team-names";
import { computeLockAt, formatLockTime, minutesUntil, formatCountdown } from "@/lib/lock";
import { MINUS } from "@/lib/units";
import styles from "./TopPlayHero.module.css";

interface TopPlayHeroProps {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  /** Slate date (ISO). The hero follows the date picker like the board. */
  date: string;
  /** The #1 system's real-money report, loaded server-side. Null when
   *  the ledger could not be read; the hero then omits the credentials
   *  row rather than inventing one. */
  report: TopPickReport | null;
}

function lookupDetail(
  r: BoardRow,
  details: Record<string, GameDetail>,
): GameDetail | undefined {
  return (
    (r.gamePk && details[r.gamePk]) ||
    details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
    details[`${r.away}@${r.home}`]
  );
}

const parseOdds = (raw: string | undefined | null): number | null => {
  const n = raw ? Number.parseFloat(String(raw).trim()) : NaN;
  return Number.isFinite(n) && n !== 0 ? n : null;
};

/** "6" -> "6u", "6.5" -> "6.5u" -- stake chips drop trailing zeros so the
 *  headline number reads like speech ("six units"), not like a ledger. */
const fmtStake = (u: number): string =>
  `${u.toFixed(2).replace(/\.?0+$/, "")}u`;

const pct = (f: number): string => `${(f * 100).toFixed(1)}%`;

export function TopPlayHero({ rows, details, date, report }: TopPlayHeroProps) {
  const pick = useMemo(() => {
    const candidates = rows
      .filter(
        (r) =>
          r.pickStrength === "STRONG" &&
          (r.pickSide === "NRFI" || r.pickSide === "YRFI"),
      )
      .map((r) => {
        const d = lookupDetail(r, details);
        const odds = parseOdds(
          r.pickSide === "NRFI" ? d?.marketNrfiOdds : d?.marketYrfiOdds,
        );
        return {
          key: r,
          name: `${r.away}@${r.home}`,
          side: r.pickSide,
          modelP: r.nrfiP,   // FULL precision -- never nrfiPct (display, 1dp)
          odds,
        };
      });
    return selectTopPick(candidates);
  }, [rows, details]);

  const season = report?.windows?.[0] ?? null;
  const last10 = report?.last10 ?? null;

  // ---- No qualifying play on this slate ------------------------------
  if (!pick) {
    const pendingCount = rows.filter((r) =>
      /PENDING/i.test(r.pickStrength || ""),
    ).length;
    const stillForming = pendingCount > 0;
    return (
      <section className={styles.hero} aria-label="The number-one play">
        <div className={styles.rules} aria-hidden />
        <div className={styles.eyebrowRow}>
          <span className={styles.eyebrow}>The №1 play</span>
          <span className={styles.eyebrowDate}>{formatLongDate(date)}</span>
        </div>
        <h2 className={styles.headlineQuiet}>
          {rows.length === 0
            ? "No games on this slate."
            : stillForming
              ? "The board is still forming."
              : "No play tonight."}
        </h2>
        <p className={styles.deck}>
          {rows.length === 0
            ? "Nothing was scheduled for this date."
            : stillForming
              ? `Lineups are pending on ${pendingCount} game${pendingCount === 1 ? "" : "s"}. Picks commit 60 minutes before first pitch.`
              : "The model looked at every game and declined them all. A quiet night is a correct outcome."}
        </p>
        <Credentials season={season} last10={last10} report={report} />
      </section>
    );
  }

  // ---- The play ------------------------------------------------------
  const row = pick.key;
  const d = lookupDetail(row, details);
  const sideP = row.pickSide === "NRFI" ? row.nrfiPct / 100 : row.yrfiPct / 100;
  const odds = pick.odds;
  /* THE LEDGER IS THE AUTHORITY ON THE STAKE. Read what the system
     actually booked; only fall back to recomputing for a row it has not
     staked yet.

     WHY (live contradiction, 2026-08-06): this hero showed 3u for
     SD@ARI while the Discord post showed 4u -- same bet, same -135,
     same formula. The raw quarter-Kelly stake was 3.4975 units, a hair
     under the 3.5 rounding boundary, and the two surfaces fed it
     probabilities of different PRECISION: this hero uses `yrfiPct`
     (63.4, one decimal) while Discord used implied(price)+edge
     (0.6343). 0.0003 of probability moved the published stake by a
     whole unit. Rounding boundaries are dense, so ANY surface that
     recomputes will eventually land on the wrong side of one. The fix
     is not better rounding -- it is that every surface prints the SAME
     STORED NUMBER. */
  /* T8.33: `!= null`, NOT `> 0`. A recorded zero is a DELIBERATE REFUSAL
     (no edge at the price, or the daily cap left no room), not a missing
     figure — the three states of `units_risked` must not collapse into
     two, which is the T8.30 incident. Sending a refusal into the
     recompute prints the uncapped stake for a bet nobody placed, in the
     hero, at 20px. */
  const stake = d?.unitsRisked != null
    ? d.unitsRisked
    : (odds != null ? stakeUnitsFor(sideP, odds) : null);
  const ladder = odds != null ? buildPriceLadder(sideP, odds) : null;
  const graded = (d?.gradedResult || "").trim().toUpperCase();
  const placed = d?.betPlaced === "Y";
  const won = graded === "WIN";
  const lost = graded === "LOSS";
  const settled = won || lost;
  const payout = (o: number): number => (o > 0 ? o / 100 : 100 / -o);
  const settledPnl =
    settled && odds != null && stake != null
      ? won
        ? stake * payout(odds)
        : -stake
      : null;

  const lockAt = computeLockAt(row.gameTimeEt || "", date);
  const minsToLock = lockAt ? minutesUntil(lockAt) : null;

  return (
    <section className={styles.hero} aria-label="The number-one play">
      <div className={styles.rules} aria-hidden />
      <div className={styles.eyebrowRow}>
        <span className={styles.eyebrow}>The №1 play</span>
        <span className={styles.eyebrowDate}>{formatLongDate(date)}</span>
      </div>

      <div className={styles.headlineRow}>
        <h2 className={styles.headline}>
          {cityOf(row.away)} <span className={styles.at}>at</span>{" "}
          {cityOf(row.home)}
        </h2>
        {settled && settledPnl != null && (
          <span
            className={styles.stamp}
            data-tone={won ? "gain" : "loss"}
            aria-label={won ? "This play won" : "This play lost"}
          >
            {won ? "WON" : "LOST"}{" "}
            {won ? "+" : MINUS}
            {Math.abs(settledPnl).toFixed(2)}u
          </span>
        )}
      </div>

      <p className={styles.deck}>
        {row.pickSide === "YRFI"
          ? "The bet: a run scores in the first inning."
          : "The bet: the first inning stays scoreless."}{" "}
        <span className={styles.deckDim}>
          Model has it {pct(sideP)} likely
          {odds != null
            ? `; the price needs ${pct(impliedOf(odds))}.`
            : "."}
        </span>
      </p>

      <div className={styles.moneyLine} data-settled={settled ? "1" : undefined}>
        <span className={styles.moneyItem}>
          <span className={styles.moneyLabel}>side</span>
          <span className={styles.moneyValue}>{row.pickSide}</span>
        </span>
        <span className={styles.moneyItem}>
          <span className={styles.moneyLabel}>price</span>
          <span className={styles.moneyValue}>
            {odds != null ? formatAmerican(odds) : "no price yet"}
          </span>
        </span>
        <span className={styles.moneyItem}>
          <span className={styles.moneyLabel}>stake</span>
          <span className={styles.moneyValueStrong}>
            {stake != null && stake > 0 ? fmtStake(stake) : "—"}
          </span>
        </span>
        {!settled && ladder != null && (
          <span className={styles.moneyItem}>
            <span className={styles.moneyLabel}>bet up to</span>
            <span className={styles.moneyValueStrong}>
              {formatAmerican(ladder.passAt)}
            </span>
          </span>
        )}
        <span className={styles.moneyItem}>
          <span className={styles.moneyLabel}>first pitch</span>
          <span className={styles.moneyValue}>{row.gameTimeEt || "—"}</span>
        </span>
      </div>

      {/* One status sentence. Never two states at once. */}
      {!settled && (
        <p className={styles.status}>
          {placed ? (
            <>The bet is in the ledger at {odds != null ? formatAmerican(odds) : "its captured price"}.</>
          ) : odds == null ? (
            <>Waiting on a price — the stake is sized the moment one is captured.</>
          ) : minsToLock != null && minsToLock > 0 ? (
            <>
              Pick locks at {lockAt ? formatLockTime(lockAt) : "—"} ET ·{" "}
              {formatCountdown(minsToLock)} from now.
            </>
          ) : (
            <>Pick is locked for first pitch.</>
          )}
        </p>
      )}

      <div className={styles.actions}>
        <a className={styles.briefBtn} href="/brief">
          Read tonight&apos;s brief
        </a>
        <OtherPlays rows={rows} details={details} exclude={row} />
      </div>

      <Credentials season={season} last10={last10} report={report} />
    </section>
  );
}

function impliedOf(odds: number): number {
  return odds < 0 ? -odds / (-odds + 100) : 100 / (odds + 100);
}

/** The rest of the card, one quiet line: every other STRONG play. */
function OtherPlays({
  rows,
  details,
  exclude,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  exclude: BoardRow;
}) {
  const others = rows.filter(
    (r) =>
      r !== exclude &&
      r.pickStrength === "STRONG" &&
      (r.pickSide === "NRFI" || r.pickSide === "YRFI"),
  );
  if (others.length === 0) return null;
  return (
    <span className={styles.others}>
      Also on the card:{" "}
      {others.map((r, i) => {
        const d = lookupDetail(r, details);
        const odds = parseOdds(
          r.pickSide === "NRFI" ? d?.marketNrfiOdds : d?.marketYrfiOdds,
        );
        const p = r.pickSide === "NRFI" ? r.nrfiPct / 100 : r.yrfiPct / 100;
        // Same rule as the hero above: booked stake wins over a recompute,
        // and a recorded zero is a refusal rather than a missing figure
        // (T8.33). The render below already prints nothing for stake <= 0.
        const stake = d?.unitsRisked != null
          ? d.unitsRisked
          : (odds != null ? stakeUnitsFor(p, odds) : null);
        return (
          <span key={`${r.away}@${r.home}#${r.gameNumber}`} className={styles.otherPlay}>
            {i > 0 && " · "}
            {r.away}@{r.home} {r.pickSide}
            {odds != null ? ` ${formatAmerican(odds)}` : ""}
            {stake != null && stake > 0 ? ` · ${fmtStake(stake)}` : ""}
          </span>
        );
      })}
    </span>
  );
}

/** The #1 system's record — REAL ledger money, same source as /history's
 *  lead section. This row is why the play above it deserves the front
 *  page: the system has receipts, and they travel with it. */
function Credentials({
  season,
  last10,
  report,
}: {
  season: TopPickReport["windows"][number] | null;
  last10: TopPickReport["last10"] | null;
  report: TopPickReport | null;
}) {
  if (!season || !report) return null;
  return (
    <div className={styles.creds}>
      <span className={styles.credsLabel}>The №1 system</span>
      <span className={styles.credsFigs}>
        <b>
          {season.wins}
          {MINUS}
          {season.losses}
        </b>{" "}
        season · hits <b>{pct(season.hitRate)}</b>
        <span className={styles.credsDim}> (needs {pct(season.breakEven)})</span>
        {" · "}
        <b data-money={report.totals.atKelly >= 0 ? "up" : "down"}>
          {report.totals.atKelly >= 0 ? "+" : MINUS}
          {Math.abs(report.totals.atKelly).toFixed(1)}u
        </b>{" "}
        at ¼-Kelly
        {last10 && (
          <>
            {" · last 10 "}
            <b>
              {last10.wins}
              {MINUS}
              {last10.losses}
            </b>
          </>
        )}
      </span>
      <a className={styles.credsLink} href="/history">
        Full record →
      </a>
    </div>
  );
}

function formatLongDate(iso: string): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}
