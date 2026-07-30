"use client";

import { useMemo, useState } from "react";
import type { RoiResponse, RoiWindow } from "@/lib/roi";
// The underwater plot and the divergence bar live in InsightCharts, which
// owns all three of the 2026-07-28 charts and their shared chrome.  Each
// renders its OWN complete card -- mount it directly, never inside a
// .chartCard, or you get a card inside a card.
import {
  UnderwaterChart,
  DivergenceBar,
  type DivergenceSummary,
} from "./InsightCharts";
import type { RecFile, RecSide } from "@/lib/season-record";
import { replayWindow, isNum } from "@/lib/season-record";
/* THE UNIT MODEL LIVES IN ONE FILE NOW (2026-07-30). This page used to
   own a private `formatUnits`, which is how it managed to print a
   season total in units on four separate surfaces: nothing could tell
   it not to. lib/units exports a `formatUnits` that REFUSES a figure
   branded as summed-across-days, so those four are now compile errors
   rather than plausible-looking numbers. */
import {
  formatUnits, formatLevel, formatBankGrowth, formatReturn,
  returnAsUnits, bankReturn, MINUS, EM_DASH,
} from "@/lib/units";
import { WeekAtAGlance } from "./WeekAtAGlance";
import styles from "./HistoryView.module.css";

const WINDOWS: { key: RoiWindow; label: string }[] = [
  { key: "7d",     label: "Last 7 days" },
  { key: "30d",    label: "Last 30 days" },
  { key: "season", label: "Season" },
];

/** The bank every replay opens with, and the definition of a unit: the
 *  bankroll is 100 units, always, so a unit is 1% of it. Charts anchor
 *  their axis here rather than at zero -- zero is not a meaningful
 *  gridline for a bankroll. */
const START_BANK = 100;

/* 2026-07-30 -- `cumulative` IS GONE, and its replacement is not a
   rename.

   It held "units since the season opener", i.e. a sum of daily unit
   figures, and it fed the ledger's third column, the whole equity
   curve, and all three stat cells under it. Under the re-based unit
   model that quantity is not money: the replay compounds the unit
   COUNT, so a 10.00u day at a 217u bank and a 2.00u day at a 223u bank
   are amounts in different currencies and adding them means nothing.

   `bank` is a LEVEL -- what the bankroll stood at when that day closed
   -- and a level is a fact about one instant, so it survives re-basing
   untouched. It is also what a subscriber can act on: "100 became
   209.89" is true for a $1k bank and a $25k bank alike.

   `units` is now RE-BASED as well: that day's P&L over the bank it
   OPENED with. It has to be, or this table and the week card at the top
   of the page print different numbers for the same night -- 2026-07-28
   was -10.00u raw and is -4.61u re-based, and both were on screen. */
interface DayRecord {
  date: string;
  /** That day's return on the bank it opened with, on a 100u bank.
   *  One night, one bank: safe to print with a "u". */
  units: number;
  /** The bank at that day's close. A level, never a sum. */
  bank: number;
}

type SeriesPoint = { date: string; units: number };

/** 2026-07-28 AUDIT FIX — THE HEADLINE WAS +34.5u WRONG.
 *
 *  Every figure on this page (net units tile, equity curve, all six
 *  drawdown stats, the daily ledger) was built from `cumulativePL`, the
 *  RAW P&L column.  That column includes 177 graded bets that settled
 *  against a FABRICATED -110 because no DraftKings price was ever
 *  captured for them, and those 177 carry +34.90u.  The page therefore
 *  read +23.94u for a season whose real-priced ledger is -10.59u --
 *  opposite signs, same bets.
 *
 *  `realPricedCumulativePL` (lib/roi.ts) is the same accumulator run over
 *  only the bets whose picked side had a captured price.  That is THE
 *  money series and it is what everything below reads.  The raw column
 *  survives as one dashed, explicitly-labelled comparison line on the
 *  equity chart so nothing appears to have silently vanished.
 *
 *  2026-07-29 -- THE PRODUCER NEVER SHIPPED.  For a day this read
 *  `realPricedCumulativePL` through a cast:
 *
 *      data as RoiResponse & { realPricedCumulativePL?: SeriesPoint[] }
 *
 *  `lib/roi.ts` never produced that field, so the fallback below fired
 *  on EVERY render: the page charted the fabricated -110 series, the
 *  headline read +21.86u while the zone table underneath it summed to
 *  -12.67u, and the banner told the operator to "reload the page to
 *  pick up the real-priced figure" -- which could never work.
 *
 *  The cast is why it went unnoticed. An OPTIONAL property on an
 *  inline intersection type cannot fail to compile when the producer
 *  omits it; there was no type error to catch, only a silent undefined.
 *  Both fields are now declared on RoiResponse itself and read
 *  directly, so deleting the producer is a compile error.
 *
 *  The fallback is kept for one narrow case only -- a browser holding a
 *  cached /api/roi payload from before this shipped -- and it now
 *  degrades honestly instead of promising a reload will fix it. */
function pickMoneySeries(data: RoiResponse): { points: SeriesPoint[]; isReal: boolean } {
  const s = data.realPricedCumulativePL;
  if (Array.isArray(s) && s.length > 0) return { points: s, isReal: true };
  return { points: data.cumulativePL ?? [], isReal: false };
}

/** THE SERIES EVERY CHART ON THIS PAGE READS (2026-07-30).
 *
 *  Operator: "all the charts should reflect the kelly sizing with the
 *  new system going back to the start of the season -- what our system
 *  would have picked and our profit."
 *
 *  So the page stops charting the realised ledger and charts the
 *  REPLAY: today's rules, quarter-Kelly, compounding from a 100u bank,
 *  real captured prices only. The ledger it used to chart is mostly
 *  bets placed under rules that no longer exist -- NRFI was live then
 *  and is off now, and the YRFI gate has since tightened -- which is
 *  exactly why the operator stopped caring about it.
 *
 *  Everything downstream (equity curve, drawdown, days-under-water,
 *  daily table) derives from this one array, so switching it here
 *  rewires the whole page rather than four charts one at a time.
 *
 *  `simBankAfter` is the compounding bank at each day's close; the
 *  chart wants profit-from-start, hence the subtraction. As of the
 *  2026-07-30 exporter fix that bank is YRFI-only, matching the system
 *  that is actually run -- before it, this would have charted a bank
 *  that staked NRFI. */
function replaySeries(
  side: RecSide | null,
  startBank: number,
  startIso: string | undefined,
  endIso: string | undefined,
): SeriesPoint[] {
  if (!side || !startIso || !endIso) return [];
  const out: SeriesPoint[] = [];
  for (const d of side.days) {
    if (d.date < startIso || d.date > endIso) continue;
    if (!isNum(d.simBankAfter)) continue;
    out.push({ date: d.date, units: d.simBankAfter - startBank });
  }
  return out;
}

/** First date at which stakes stopped being flat 1u.  Before it a loss is
 *  -1.00u; after it a loss is whatever the stake was. */
function stakeEpochOf(data: RoiResponse): string | null {
  return data.stakeEpoch ?? null;
}

/** Bankroll history for the production V2.1 model.  T-V21-LOCKIN-2026-05-06
 *  removed the v2/v3 split (V3 was Variant K shadow, no longer surfaced). */
export function HistoryView({
  initial,
  seasonRecord,
  divergence,
}: {
  initial: RoiResponse;
  /** data/season_record.json, read server-side by app/history/page.tsx.
   *  Optional: null simply omits the system card, never breaks the page. */
  seasonRecord?: RecFile | null;
  /** Replay-vs-ledger census, read server-side from season_record.json
   *  and passed down by app/history/page.tsx.  Optional: when it is not
   *  supplied the divergence card is simply not mounted, so this page
   *  keeps rendering on its own. */
  divergence?: DivergenceSummary | null;
}) {
  const [data, setData]     = useState<RoiResponse>(initial);
  const [window, setWindow] = useState<RoiWindow>(initial.window);
  const [loading, setLoading] = useState(false);

  async function changeWindow(w: RoiWindow) {
    if (w === window) return;
    setWindow(w);
    setLoading(true);
    try {
      const url = `/api/roi?window=${w}`;
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) setData((await res.json()) as RoiResponse);
    } finally {
      setLoading(false);
    }
  }

  const eyebrowText = "Performance · daily breakdown";
  const titleText   = "Bankroll history";

  // THE PAGE'S SERIES. Replay first (see replaySeries); the realised
  // ledger survives only as a fallback for a payload with no record.
  const money = useMemo(() => {
    const replay = replaySeries(
      seasonRecord?.real ?? seasonRecord?.projected ?? null,
      seasonRecord?.startBank ?? 100,
      data.startDate, data.endDate,
    );
    if (replay.length > 0) return { points: replay, isReal: true, isReplay: true };
    return { ...pickMoneySeries(data), isReplay: false };
  }, [seasonRecord, data]);
  const stakeEpoch = stakeEpochOf(data);

  // REAL prices first, matching RoiPanel's SystemCard. `projected` fills
  // a third of its book at an assumed -125; leading with it here would
  // reintroduce on this page the exact figure that was removed from the
  // dashboard headline.
  const sysSide = seasonRecord?.real ?? seasonRecord?.projected ?? null;
  const sysBank = seasonRecord?.startBank ?? 100;
  const sysWindow = useMemo(
    () => replayWindow(sysSide, data.startDate, data.endDate),
    [sysSide, data.startDate, data.endDate],
  );

  // sysDaily() was deleted 2026-07-30 with the System column it fed.
  // The whole page charts the replay now, so every row IS the system
  // and a separate per-day system figure is the same number twice.

  // Per-day records.
  //
  // The daily figure is READ from the record (`simPnl`), not derived by
  // differencing the cumulative. Differencing seeds `prev` at 0, so on
  // any window that does not start at the season opener the OLDEST
  // visible row reported its entire season-to-date cumulative as that
  // single day's P&L -- "Last 30 days" showed a +150u day. Reading the
  // stored per-day figure has no such edge case and is exact.
  //
  // `cumulative` stays absolute (bank minus the 100u start), i.e.
  // profit since the season opener, which is meaningful in every window.
  // It is deliberately NOT window-relative: the operator asked for the
  // curve "going back to the start of the season".
  const days = useMemo<DayRecord[]>(() => {
    const side = seasonRecord?.real ?? seasonRecord?.projected ?? null;
    const bank0 = seasonRecord?.startBank ?? 100;
    if (side && money.isReplay) {
      const out: DayRecord[] = [];
      for (const d of side.days) {
        if (d.date < data.startDate || d.date > data.endDate) continue;
        if (!isNum(d.simBankAfter)) continue;
        const raw = isNum(d.simPnl) ? d.simPnl : 0;
        // The bank the day OPENED with -- the denominator that makes
        // this figure comparable across a compounding run.
        const before = d.simBankAfter - raw;
        out.push({
          date: d.date,
          units: before > 0 ? (raw / before) * 100 : 0,
          bank: d.simBankAfter,
        });
      }
      return out;
    }
    // LEDGER FALLBACK (no record at all). The roi series is a running
    // total of realised units, so the bank is the opening bank plus it.
    // Differencing for the daily figure is fine because the series is
    // already windowed and starts at 0.
    const out: DayRecord[] = [];
    let prev = 0;
    for (const row of money.points) {
      const before = bank0 + prev;
      out.push({
        date: row.date,
        units: before > 0 ? ((row.units - prev) / before) * 100 : 0,
        bank: bank0 + row.units,
      });
      prev = row.units;
    }
    return out;
  }, [money.points, money.isReplay, seasonRecord, data.startDate, data.endDate]);

  /* THE WINDOW'S RESULT, from the bank's two endpoints rather than from
     adding up `days`. Daily returns COMPOUND, they do not sum -- the
     last seven days are -0.90, 0.00, -1.81, -4.61, +1.37, which add to
     -5.95 and compound to -5.91. Taking the ratio is exact and needs no
     apology; taking the sum is a different number that happens to look
     close on a quiet week and diverges badly on a loud one. */
  const windowBank = useMemo(() => {
    if (days.length === 0) return null;
    const bank0 = seasonRecord?.startBank ?? 100;
    // The opening bank is the close of the day BEFORE the window, which
    // for the first row is its own close minus its own move.
    const first = days[0];
    const openBank = first.units !== 0 ? first.bank / (1 + first.units / 100) : first.bank;
    const endBank = days[days.length - 1].bank;
    return { openBank, endBank, seasonStart: bank0, ret: bankReturn(openBank, endBank) };
  }, [days, seasonRecord]);

  // How many of the graded bets behind this page carry a real captured
  // price, and how many settled against the fabricated -110.  Summed over
  // the same population the money series counts: bet zones, LEAN excluded
  // (LEAN is never wagered, so it never moves the bankroll).
  const priceSplit = useMemo(() => {
    let real = 0;
    let assumed = 0;
    for (const z of data.betZones ?? []) {
      if (z.strength === "LEAN") continue;
      real    += z.provenance.realPricedBets;
      assumed += z.provenance.placeholderBets;
    }
    return { real, assumed };
  }, [data.betZones]);

  // Which sides actually make up the number, and what each one did on
  // real prices.  Derived from the zone provenance, never asserted: the
  // operator has been told "the season is down" without ever being told
  // WHICH book is down, and the two sides point opposite ways.
  const composition = useMemo(() => {
    // 2026-07-29 -- INCLUDE THE PASS ZONES THAT CARRY REAL MONEY.
    //
    // This read `betZones` only, which excludes anything whose pick_side
    // is PASS. But a PASS-labelled row CAN hold a real bet at a real
    // price: a STRONG pick whose label was still "LINEUP PENDING" when
    // the lock window closed gets bet_placed=Y and settles normally
    // (2026-07-27 NYY@CWS, +0.909u, is one).
    //
    // Season-wide that is +1.62u over 3 bets, and dropping it made this
    // line sum to -10.55u under a headline reading -8.93u. Two figures
    // disagreeing by 1.62u on one screen is the exact defect this page
    // was being fixed for, so the split now covers the SAME population
    // the headline does.
    const withMoney = [...(data.betZones ?? []), ...(data.passZones ?? [])]
      .filter((z) => z.strength !== "LEAN" && z.provenance.realPricedBets > 0);
    const parts = withMoney.map(
      (z) =>
        `${z.label} ${zoneReturnText(z)} over ` +
        `${z.provenance.realPricedBets} ` +
        `${z.provenance.realPricedBets === 1 ? "bet" : "bets"}`,
    );
    if (parts.length === 0) return undefined;
    return (
      "Split by side, counting only bets that had a real captured price: " +
      `${parts.join(" · ")}.`
    );
  }, [data.betZones, data.passZones]);

  // `totalUnits` and `totalDays` deleted 2026-07-30. totalUnits was the
  // season unit total and had no reader; totalDays duplicated
  // stats.totalDays. Both were dead, and the dead one was the dangerous
  // one -- a computed season total sitting in scope is an invitation.
  const bestDay  = days.length ? Math.max(...days.map((d) => d.units)) : 0;
  const worstDay = days.length ? Math.min(...days.map((d) => d.units)) : 0;

  // Reverse-chronological for the table
  const tableRows = [...days].reverse();

  return (
    <main className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.headLeft}>
          <a href="/" className={styles.backLink} aria-label="Back to slate board">
            <span aria-hidden>◂</span> Slate
          </a>
          <div>
            <div className={styles.eyebrow}>{eyebrowText}</div>
            <h1 className={styles.title}>{titleText}</h1>
          </div>
        </div>
        <div className={styles.headRight}>
          <div className={styles.windowToggle} role="tablist" aria-label="Time window">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                role="tab"
                aria-selected={window === w.key}
                onClick={() => changeWindow(w.key)}
                className={`${styles.windowBtn} ${
                  window === w.key ? styles.windowBtnActive : ""
                }`}
                type="button"
                disabled={loading}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* THE SYSTEM, for the SAME window the filters above select.
          Added 2026-07-29: this page had the identical 7d/30d/season
          filters as the main dashboard but answered a different
          question with them -- it only ever showed the realised ledger,
          which is mostly bets placed under RULES THAT NO LONGER APPLY
          (NRFI was live then and is switched off now; the YRFI gate has
          tightened). So the operator could pick "Last 30 days" on two
          pages and get two unrelated numbers with nothing saying why.

          This card answers "what would today's system have done over
          this window". The ledger below answers "what actually
          happened". Both are true, they are different questions, and
          they are now labelled as such instead of being left to look
          like a contradiction. */}
      {/* ONE HERO, NOT TWO PEERS (2026-07-29 redesign pass 2).
          These were two identically-weighted bordered tiles stacked on
          top of each other -- the system's figure and the ledger's --
          which is precisely the "stack of same-weight cards" PRODUCT.md
          names as the reason nothing on this page is scannable, and it
          also made two numbers that answer DIFFERENT questions look like
          a contradiction.

          Now: the system leads at full size because that is what this
          page is for, and what actually happened sits beneath it as a
          subordinate line rather than a rival card. The date filter
          drives both. */}
      <section className={styles.hero}>
        {sysWindow ? (
          <>
            <div className={styles.heroLabel}>
              The system · ¼-Kelly <span className="tag">Simulated</span>
            </div>
            {/* THE RETURN, NOT THE SUM (2026-07-30, unit re-basing).
                This printed `sysWindow.yrfi.pnl` -- units added across
                every day in the window. Under the re-based model that
                is not a money quantity, and the field is now branded so
                that printing it does not compile.

                What replaces it is the same number the bank moved by,
                expressed on a 100-unit bank. That is legitimate for a
                multi-day window ONLY because a unit is 1% of bank by
                definition, so a percentage and a unit count are the
                same figure. For the SEASON window the two happen to
                agree (the run opens at exactly 100u); for "Last 30
                days" they do not, and the old figure was the wrong one.

                The bank endpoints stay in the sub-line and are now
                doing real work: they are where this number comes from,
                not decoration. */}
            <div className={styles.heroFig}>
              {windowBank?.ret != null
                ? returnAsUnits(windowBank.ret)
                : formatLevel(0)}
            </div>
            <div className={styles.heroSub}>
              {windowBank?.ret != null && (
                <>{formatReturn(windowBank.ret)} of bank · </>
              )}
              {sysWindow.yrfi.wins}-{sysWindow.yrfi.bets - sysWindow.yrfi.wins} over{" "}
              {sysWindow.yrfi.bets} {sysWindow.yrfi.bets === 1 ? "bet" : "bets"} ·{" "}
              {sysWindow.from} → {sysWindow.to}
              {/* THE BANK'S JOURNEY, not "on a NNNu bank" (2026-07-30).
                  Operator: "the total units profit should be based off
                  of starting with 100units. it doesnt make sense where
                  you say 'bank it was earned off of'." Right -- naming a
                  232u base implies the run started there. It started at
                  100u; 232u is just where it had got to when the window
                  opened. Showing the two endpoints says that without
                  introducing a second base. */}
              {windowBank && (
                <> · bank {formatBankGrowth(windowBank.openBank, windowBank.endBank)}</>
              )}
            </div>
            {/* THE UNLEVERED ("flat 1u") LINE WAS REMOVED 2026-07-30.
                Operator: "i wanted to remove the flat unit tracking in
                the dashboard". The system stakes quarter-Kelly; a flat
                figure describes a staking scheme nobody runs, and
                carrying it invited exactly the levered-vs-unlevered
                confusion it was added to resolve. The data still
                exists -- season_record.json keeps `flatProfit` per side
                and per month -- so the question is one command away via
                tools/kelly_season_backfill.py. */}
          </>
        ) : (
          <>
            <div className={styles.heroLabel}>The system · ¼-Kelly</div>
            <div className={styles.heroFig}>—</div>
            <div className={styles.heroSub}>
              No replayed bets in this window.
            </div>
          </>
        )}

        {/* THE "WHAT ACTUALLY HAPPENED" LINE WAS REMOVED 2026-07-30
            with the rest of the old-ledger surfacing. Operator: "i
            dont care about my old bets ... all i care about is the new
            system, the new kelly sizing, and the new total profit."
            That figure was the realised ledger, which is dominated by
            bets placed under rules that no longer exist -- 49 NRFI bets
            worth -11.29u from a side switched off on 2026-06-07, plus
            142 YRFI bets today's gate would reject. Every row is still
            in the CSV and Supabase; tools/pl_calc.py reports it. */}
      </section>

      {/* WEEK AT A GLANCE -- above the equity curve on purpose.
          The curve answers "how has the season gone"; this answers "how
          did the last week go", which is the question someone opening
          this page most often actually has, and it answers it in ONE
          figure. It is deliberately fixed at 7 days and does NOT follow
          the window toggle above: a card captioned "last 7 days" that
          silently becomes 30 is the two-figures-one-label trap this
          page has been cleared of twice.

          `sysSide` is the same side object the hero reads, so the two
          cannot describe different books. Renders nothing at all when
          there is no record -- see the guard in the component. */}
      <WeekAtAGlance side={sysSide} />

      {/* T2.42: Bankroll equity curve.  Pure cumulative line + drawdown
          shading + peak marker + stats panel.  Reads the real-priced
          series; the raw column rides along as a labelled dashed line. */}
      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <div className={styles.eyebrow}>Bankroll equity curve</div>
            <div className={styles.chartTitle}>
              Cumulative units over time · drawdown vs all-time high
            </div>
          </div>
          <div className={styles.legend}>
            <span className={styles.legendItem}>
              <span className={styles.legendLine} data-tone="equity" /> Equity
            </span>
            <span className={styles.legendItem}>
              <span className={styles.legendDot} data-tone="peak" /> All-time high
            </span>
            <span className={styles.legendItem}>
              <span className={styles.legendSwatch} data-tone="drawdown" /> Drawdown
            </span>
            {money.isReal && (
              <span
                className={styles.legendItem}
                title={
                  "The same days, counting every graded bet including the " +
                  "ones that had no captured DraftKings price and were " +
                  "settled against an assumed -110. Shown only so the older, " +
                  "higher number is still visible; it is not your ledger."
                }
              >
                <span className={styles.legendLine} data-tone="assumed" /> Includes
                assumed &minus;110 prices
              </span>
            )}
          </div>
        </div>
        <EquityCurveChart
          days={days}
          rawSeries={money.isReal ? data.cumulativePL : []}
        />
      </section>

      {/* CHART 2 -- underwater / drawdown depth.  Depth AND age, on a fixed
          axis.  At compounding stakes these are the two numbers that decide
          whether the operator can keep running the system, and the equity
          curve buries both: a line that wanders can be read optimistically,
          an underwater plot cannot.  Renders its own card. */}
      {/* FED FROM `days`, not from `money.points` (2026-07-30).
          Two reasons. The chart now measures depth as a share of the
          peak BANK, so it needs bank levels rather than the cumulative
          unit series it used to take. And `days` is already the
          windowed, re-based series that the equity curve and the ledger
          table below both read -- routing this chart through it means
          three surfaces cannot disagree about which days are in the
          window, which they could while there were two arrays. */}
      <UnderwaterChart
        series={days.map((d) => ({ date: d.date, bank: d.bank }))}
        stakeEpoch={stakeEpoch}
        composition={composition}
      />

      {/* CHART 3 -- replay vs ledger divergence.  Only mounted when the
          server handed us the census; the card renders nothing useful
          without it, and an empty card is worse than no card. */}
      {divergence && (
        <DivergenceBar
          summary={divergence}
          contrastNote={
            "This card counts the games in the season record's real-price " +
            "window. The net units above count a different population — " +
            "every real-priced graded bet of the whole season — which is " +
            "why the two do not add up to the same figure."
          }
        />
      )}

      {/* T4.17: Win-rate by zone */}
      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <div className={styles.eyebrow}>Hit rate by pick zone</div>
            <div className={styles.chartTitle}>
              Wins / bets per zone vs the break-even rate at the prices you
              actually paid
            </div>
          </div>
        </div>
        <ZoneHitRateChart zones={data.betZones} />
      </section>

      {/* Table */}
      <section className={styles.tableCard}>
        <div className={styles.eyebrow}>Daily ledger</div>
        <div className={styles.tableWrap}>
          {/* "Day P&L" became "You" and gained a "System" column
              (2026-07-29). The old heading did not say WHOSE P&L, so a
              table ending at -4.41u sat under a hero reading +14.54u
              and looked like an arithmetic error. It was not -- the two
              are different populations, and now each column sums to its
              own headline. */}
          {/* Four tracks again (2026-07-30). The System/You split existed
              to reconcile a replay headline against a ledger table; the
              whole page is the replay now, so there is nothing to
              reconcile and the second pair was two columns of the same
              retired ledger. */}
          <div className={styles.theadRow}>
            <div>Date</div>
            <div className={styles.right}>Day</div>
            <div className={styles.right}>Bank</div>
            <div className={styles.barCell}>Distribution</div>
          </div>
          {tableRows.length === 0 ? (
            <div className={styles.empty}>
              No graded bets in this window yet.
            </div>
          ) : (
            tableRows.map((d) => (
              <DayRow
                key={d.date}
                day={d}
                maxAbs={Math.max(Math.abs(bestDay), Math.abs(worstDay), 0.01)}
              />
            ))
          )}
        </div>
        <div className={styles.tableFoot}>
          {/* REWRITTEN 2026-07-30. The old copy described a System/You
              column pair deleted earlier the same day, and claimed the
              column "sums to the figure at the top of this page" --
              which is now exactly the thing that must never be said.
              Daily returns COMPOUND; they do not add. The last seven
              days add to −5.95 and compound to −5.91, and on a loud
              week the gap is much wider. */}
          <b>Day</b> is that night&apos;s move as a share of the bank it
          opened with, so it means the same thing in April and in July.
          <b> Bank</b> is where the bankroll stood at the close. Day figures
          compound rather than add, so they will not total to the change in
          the Bank column — that is what compounding is, not a rounding
          error.
          {/* EXPORT LAG, disclosed rather than left to mislead.
              System comes from the nightly season_record.json replay; You
              reads the live ledger. A game that grades AFTER the export
              is already in You and not yet in System, so the most recent
              row can show a gap that is timing, not disagreement --
              2026-07-29 HOU@LAA (+4.12u) did exactly that. It self-heals
              on the next grade cron, which re-runs the export. */}
          {" "}The System column comes from a nightly replay, so a game that
          grades late can appear under You a cycle before it appears under
          System — the newest row can lag by a day.
          {money.isReal && (
            <>
              {" "}Both count only bets that had a real captured DraftKings
              price; a day on which no price was ever captured shows 0.00u.
            </>
          )}
        </div>
      </section>
    </main>
  );
}

/* ------------- T2.42: bankroll equity curve ------------- */

interface EquityStats {
  /** Highest BANK LEVEL reached. Was the highest cumulative-units
   *  total, which under re-basing is not a quantity. */
  peak: number;
  peakDate: string | null; // ISO date of the ATH
  /** Deepest peak-to-trough fall, as a FRACTION of the peak bank.
   *  Kept as a fraction rather than units on purpose: a 20-unit fall
   *  from a 220u bank and a 20-unit fall from a 110u bank are 9% and
   *  18% -- the same "units" describing twice the damage. Percentage
   *  of peak is the only reading that holds across a compounding run,
   *  and it is what a follower on any bankroll actually experienced. */
  maxDrawdownPct: number;
  totalDays: number;
}

/* 2026-07-28: this struct lost `vol` and `sharpe` along with the two
   cells that rendered them.
     - Sharpe was (mean/stdev)*sqrt(252).  On the contaminated series it
       printed +1.54 where the real-priced answer is -0.79 -- a SIGN FLIP,
       not a mis-scale.  sqrt(252) also assumes 252 independent periods a
       year when the system bets ~116 days and none in the off-season, and
       the standard error on n=116 puts the season interval somewhere
       around [-1.31, +4.48], which cannot be told apart from zero.  A
       figure that looks rigorous and cannot be is worse than no figure.
     - Volatility was the per-day stdev across two staking regimes (366
       bets at flat 1.00u, then stakes that vary).  The stdev of a series
       whose unit changed mid-window is not a stationary quantity.
   It also lost `trough` / `currentDrawdown` / `daysAtAth`: the underwater
   chart owns the drawdown story now, and it tells it with depth AND age
   instead of a single number. */

function computeEquityStats(days: DayRecord[]): EquityStats {
  if (days.length === 0) {
    return { peak: 0, peakDate: null, maxDrawdownPct: 0, totalDays: 0 };
  }

  // Running peak per day -- once a day's cumulative exceeds the prior
  // peak, that's a new ATH.
  let runningPeak = -Infinity;
  let runningPeakDate: string | null = null;
  let maxDD = 0;          // largest peak-to-trough draw seen

  for (const d of days) {
    if (d.bank > runningPeak) {
      runningPeak = d.bank;
      runningPeakDate = d.date;
    }
    // Measured as a share of the peak IT FELL FROM, day by day, not of
    // the all-time peak at the end. A 10% fall early in the run and a
    // 10% fall late are the same experience for the follower, and only
    // the per-peak ratio says so.
    const dd = runningPeak > 0 ? (runningPeak - d.bank) / runningPeak : 0;
    if (dd > maxDD) maxDD = dd;
  }

  return {
    peak:           runningPeak === -Infinity ? 0 : runningPeak,
    peakDate:       runningPeakDate,
    maxDrawdownPct: maxDD * 100,
    totalDays:      days.length,
  };
}


function EquityCurveChart({
  days,
  rawSeries,
}: {
  days: DayRecord[];
  /** the raw P&L column, including bets settled at an assumed -110.
   *  Empty array = don't draw the comparison line. */
  rawSeries: SeriesPoint[];
}) {
  if (days.length === 0) {
    return <div className={styles.chartEmpty}>No graded days in this window.</div>;
  }

  const stats = computeEquityStats(days);

  // Layout — slightly taller than the underwater plot since this is the
  // headline view.
  const W = 1100;
  const H = 320;
  const padL = 56, padR = 12, padT = 16, padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  // The raw (assumed-price) comparison line, aligned to this chart's
  // x-axis by DATE.  Dates the real series doesn't have are skipped
  // rather than shifted, which would silently mis-date the line.
  // REBASED ONTO THE BANK AXIS (2026-07-30). The equity line is a bank
  // level starting at 100u; this comparison series is cumulative P&L
  // starting at 0. Plotted raw against the new axis it would sit a
  // whole bankroll below the curve it is supposed to be compared with.
  const rawByDate = new Map(rawSeries.map((p) => [p.date, START_BANK + p.units]));
  const rawValues = days
    .map((d) => rawByDate.get(d.date))
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));

  // Y range pinned to include 0 so "where the bankroll started" is
  // always visible, even if we've been profitable the whole window.
  // Pinned to include the OPENING BANK (100u) rather than zero, so
  // "where the bankroll started" is always on the chart. Zero is not a
  // meaningful gridline for a bank level -- a bankroll at 0 is a ruined
  // one, and anchoring there squashes the whole series into the top
  // sliver of the plot.
  const cumMax = Math.max(START_BANK, stats.peak, days[days.length - 1].bank, ...rawValues);
  const cumMin = Math.min(
    START_BANK,
    ...days.map((d) => d.bank),
    ...rawValues,
  );
  const cumRange = cumMax - cumMin || 1;

  const stepX = innerW / Math.max(days.length, 1);
  const xFor = (i: number) => padL + (i + 0.5) * stepX;
  const yFor = (v: number) => padT + innerH - ((v - cumMin) / cumRange) * innerH;
  const yZero = yFor(START_BANK);

  // Build the equity line path
  const linePath = days
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xFor(i).toFixed(1)} ${yFor(d.bank).toFixed(1)}`)
    .join(" ");

  /* 2026-07-28: the "Expected (avg trend)" line was deleted here.  Its
     slope was finalCum / (days - 1) anchored at 0 on day 0 -- i.e. the
     chord from the first point of the series to the last point of the
     SAME series.  The tooltip claimed it separated variance from drift,
     but the gap between it and equity is guaranteed to close at the right
     edge by construction, on every window, every time.  It could not
     signal anything and it actively taught a false inference. */

  // The raw-column comparison line.  Broken into segments so a missing
  // date lifts the pen instead of drawing a straight line across a gap.
  const rawPath = (() => {
    if (rawByDate.size === 0) return "";
    let out = "";
    let penDown = false;
    days.forEach((d, i) => {
      const v = rawByDate.get(d.date);
      if (typeof v !== "number" || !Number.isFinite(v)) {
        penDown = false;
        return;
      }
      out += `${penDown ? "L" : "M"} ${xFor(i).toFixed(1)} ${yFor(v).toFixed(1)} `;
      penDown = true;
    });
    return out.trim();
  })();

  // Build the area-fill path (line down to baseline = 0, back to start)
  const areaPath =
    `M ${xFor(0).toFixed(1)} ${yZero.toFixed(1)} ` +
    days.map((d, i) => `L ${xFor(i).toFixed(1)} ${yFor(d.bank).toFixed(1)}`).join(" ") +
    ` L ${xFor(days.length - 1).toFixed(1)} ${yZero.toFixed(1)} Z`;

  // Drawdown shading: for each point, draw a thin segment from the
  // running peak DOWN to the current cumulative.  We render this as
  // a single polygon: top edge follows the running-peak watermark,
  // bottom edge follows the equity line.  Only fill where peak >
  // current (i.e., we're in drawdown).
  let runningPeak = -Infinity;
  const peakLine: { x: number; y: number; v: number }[] = [];
  for (let i = 0; i < days.length; i++) {
    if (days[i].bank > runningPeak) runningPeak = days[i].bank;
    peakLine.push({ x: xFor(i), y: yFor(runningPeak), v: runningPeak });
  }
  // Shade polygons -- one per contiguous drawdown segment so we don't
  // shade the regions where equity == peak (no DD there).
  const drawdownPolys: { d: string }[] = [];
  let segStart = -1;
  for (let i = 0; i < days.length; i++) {
    const inDD = days[i].bank < peakLine[i].v;
    if (inDD && segStart < 0) segStart = i;
    if ((!inDD || i === days.length - 1) && segStart >= 0) {
      const end = inDD ? i : i - 1;
      const top = peakLine.slice(segStart, end + 1)
        .map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" L ");
      const bot = days.slice(segStart, end + 1).reverse()
        .map((d, k) => {
          const idx = end - k;
          return `${xFor(idx).toFixed(1)} ${yFor(d.bank).toFixed(1)}`;
        }).join(" L ");
      drawdownPolys.push({ d: `M ${top} L ${bot} Z` });
      segStart = -1;
    }
  }

  // Y-axis ticks
  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }).map((_, i) => {
    const t = cumMin + (cumRange * i) / (tickCount - 1);
    return { v: t, y: yFor(t) };
  });

  // X-axis labels — every Nth so they don't overlap
  const labelEvery = Math.max(1, Math.ceil(days.length / 10));

  // Find peak + current point for markers
  const peakIdx = stats.peakDate
    ? days.findIndex((d) => d.date === stats.peakDate)
    : -1;
  const lastIdx = days.length - 1;

  return (
    <>
      <div className={styles.chartScroll}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className={styles.chartSvg}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Bankroll equity curve with drawdown shading"
        >
          {/* Y-axis ticks + grid */}
          {ticks.map((t, i) => (
            <g key={i}>
              <line x1={padL} x2={W - padR} y1={t.y} y2={t.y} className={styles.gridLine} />
              <text x={padL - 8} y={t.y + 3} textAnchor="end" className={styles.axisText}>
                {/* A BANK LEVEL, so no sign: 209u, not +209u. The
                    signed form read as profit and made the 100u start
                    look like a 100u gain. */}
                {t.v.toFixed(0)}u
              </text>
            </g>
          ))}

          {/* Zero baseline (dashed) */}
          <line x1={padL} x2={W - padR} y1={yZero} y2={yZero} className={styles.gridZero} />

          {/* Drawdown polygons (shading where equity is below ATH) */}
          {drawdownPolys.map((p, i) => (
            <path key={`dd-${i}`} d={p.d} className={styles.equityDrawdown} />
          ))}

          {/* Area fill below the equity line.  Drawn FIRST so the line +
              DD shading render on top. */}
          <path d={areaPath} className={styles.equityArea} />

          {/* The raw column, including bets settled at an assumed -110.
              Dashed, muted, never toned -- it is a comparison, not money. */}
          {rawPath && <path d={rawPath} className={styles.equityAssumedLine} />}

          {/* Equity line */}
          <path d={linePath} className={styles.equityLine} />

          {/* Peak watermark line (dashed horizontal at ATH) */}
          {stats.peak > 0 && (
            <line
              x1={padL}
              x2={W - padR}
              y1={yFor(stats.peak)}
              y2={yFor(stats.peak)}
              className={styles.equityPeakLine}
            />
          )}

          {/* Peak marker (diamond) */}
          {peakIdx >= 0 && (
            <g>
              <path
                d={(() => {
                  const x = xFor(peakIdx), y = yFor(stats.peak);
                  return `M ${x} ${y - 6} L ${x + 6} ${y} L ${x} ${y + 6} L ${x - 6} ${y} Z`;
                })()}
                className={styles.equityPeakMarker}
              />
            </g>
          )}

          {/* Current point marker */}
          {lastIdx >= 0 && (
            <circle
              cx={xFor(lastIdx)}
              cy={yFor(days[lastIdx].bank)}
              r={4}
              className={styles.equityCurrentMarker}
            />
          )}

          {/* X-axis date labels */}
          {days.map((d, i) =>
            i % labelEvery === 0 ? (
              <text
                key={i}
                x={xFor(i)}
                y={H - padB + 18}
                textAnchor="middle"
                className={styles.axisText}
              >
                {d.date.slice(5)}
              </text>
            ) : null,
          )}
        </svg>
      </div>

      {/* Stats panel below the chart.  Three cells, not six: Volatility and
          Sharpe are gone (see the note on EquityStats). */}
      <div className={styles.equityStats}>
        <div className={styles.equityStatCell}>
          {/* 2026-07-30: was "Window P&L" printing cumulative units.
              A bank level needs no re-basing and no caveat -- it is
              simply where the bankroll stands, and it is the figure a
              follower on any bankroll can multiply by their own unit
              size. Unsigned, because a level is not a profit.
              (2026-07-28 note kept: this cell was ONCE labelled
              "Bankroll" while printing window P&L, which is how the
              two quantities got confused in the first place.) */}
          <div className={styles.equityStatLabel}>Bank now</div>
          <div className={styles.equityStatBig} data-tone="neutral">
            {formatLevel(days[lastIdx].bank)}
          </div>
          <div className={styles.equityStatSub}>
            from {formatLevel(START_BANK, 0)} at the season opener ·{" "}
            {stats.totalDays} {stats.totalDays === 1 ? "day" : "days"} in this window
          </div>
        </div>
        <div className={styles.equityStatCell}>
          <div className={styles.equityStatLabel}>Peak bank</div>
          {/* 2026-07-30: the peak of a BANK LEVEL, so it can never be
              negative and never needs a tone. (2026-07-28 note kept:
              data-tone was once hardcoded "pos", so a losing window
              rendered a negative all-time high in the profit colour --
              a class of bug that a level simply cannot have.) */}
          <div className={styles.equityStatBig} data-tone="neutral">
            {formatLevel(stats.peak)}
          </div>
          <div className={styles.equityStatSub}>
            {stats.peakDate ? `on ${stats.peakDate.slice(5)}` : "—"}
          </div>
        </div>
        <div className={styles.equityStatCell}>
          <div className={styles.equityStatLabel}>Max drawdown</div>
          {/* 2026-07-30: a PERCENTAGE of the peak it fell from, not a
              unit figure. A 20-unit fall from a 220u bank and a 20-unit
              fall from a 110u bank are 9% and 18% -- the same "units"
              describing twice the damage, which is exactly the drift
              re-basing exists to remove. The percentage is also the
              only form a follower on a different bankroll can use. */}
          <div
            className={styles.equityStatBig}
            data-tone={stats.maxDrawdownPct > 0 ? "neg" : "neutral"}
          >
            {stats.maxDrawdownPct > 0
              ? `${MINUS}${stats.maxDrawdownPct.toFixed(1)}%`
              : "0.0%"}
          </div>
          <div className={styles.equityStatSub}>
            {stats.maxDrawdownPct <= 0
              ? "never below the high"
              : "deepest fall from a running high"}
          </div>
        </div>
      </div>
    </>
  );
}


/* 2026-07-28: the underwater plot used to be hand-rolled here.  It now
   lives in InsightCharts.tsx alongside the reliability curve and the
   divergence bar -- one module for the three charts so they share the
   guarded formatters and the "never tone-colour a simulated figure"
   rule.  This page just mounts it with the real-priced series. */

/* ------------- T4.17 win-rate by zone ------------- */

/* realZonePL() DELETED 2026-07-30. Its only caller printed a season
   unit total; zoneReturn() below folds the same real-priced-population
   logic into the ratio that replaced it, so there is one function
   rather than two that must agree. */

/* ============================================================
   ZONE RESULTS AS A RETURN, NOT A UNIT TOTAL (2026-07-30).

   These figures sum `profit_loss_units` across a whole season of
   LEDGER rows, which is the same across-time addition the re-basing
   forbids everywhere else on this page.

   The honest denominator is what was staked to earn it, which turns
   the figure into a RETURN PER UNIT STAKED -- scale-free, comparable
   between zones with very different bet counts, and the number a
   follower on any bankroll would have experienced.

   THE DENOMINATOR IS AN ASSUMPTION AND IT IS STATED ON THE CARD.
   `ZoneProvenance` counts bets, not units risked, so this divides by
   the bet count and thereby assumes one unit a bet. That is exactly
   right for every row placed before quarter-Kelly went live on
   2026-07-28 and progressively wrong after it. It is a display fix,
   not the underlying repair: the ledger's stored `profit_loss_units`
   is still in old compounded units for the Kelly-era rows, and
   correcting THAT is a data migration through tracker.py rather than
   anything this component can do.
   ============================================================ */

/** Return per unit staked. `null` when the zone has no priced bets. */
function zoneReturn(z: import("@/lib/roi").ZoneRoi): number | null {
  const pr = z.provenance;
  const known = pr.realPricedBets + pr.placeholderBets;
  const pl = known > 0 ? pr.realPricedPL : z.unitsPL;
  const staked = known > 0 ? pr.realPricedBets : z.bets;
  if (!Number.isFinite(pl) || !Number.isFinite(staked) || staked <= 0) return null;
  return pl / staked;
}

function zoneReturnText(z: import("@/lib/roi").ZoneRoi): string {
  const r = zoneReturn(z);
  return r == null ? EM_DASH : formatReturn(r, 1);
}

/** Hit rate over the SAME population as the P&L and the break-even tick:
 *  the real-priced subset.  Returns the all-graded rate only when no bet
 *  in the zone carries a captured price (LEAN zones, where flat -110 is
 *  the correct reference). */
function realZoneHit(z: import("@/lib/roi").ZoneRoi): { rate: number; wins: number; losses: number; real: boolean } {
  const pr = z.provenance;
  const n = pr.realPricedWins + pr.realPricedLosses;
  if (pr.realPricedBets > 0 && n > 0) {
    return { rate: pr.realPricedWins / n, wins: pr.realPricedWins, losses: pr.realPricedLosses, real: true };
  }
  return { rate: z.hitRate, wins: z.wins, losses: z.losses, real: false };
}

function ZoneHitRateChart({ zones }: { zones: import("@/lib/roi").ZoneRoi[] }) {
  const withBets = zones.filter((z) => z.bets > 0);
  if (withBets.length === 0) {
    return <div className={styles.chartEmpty}>No graded bets in this window yet.</div>;
  }
  // -110 reference, used ONLY for a zone that has no captured price at all.
  const FLAT_110 = 0.524;
  const placeholderTotal = withBets.reduce((a, z) => a + z.provenance.placeholderBets, 0);
  return (
    <div className={styles.zoneChart}>
      {withBets.map((z) => {
        const hit = realZoneHit(z);
        /* 2026-07-28 AUDIT FIX: this line was hardcoded to 0.524 -- the
           break-even rate of a -110 bet -- for every zone, while the
           footnote admitted in prose that most bets were placed at worse
           prices. STRONG YRFI at 57.7% looked 5.3 points clear of the
           line and is actually 1.9 points clear of its real one (55.82%).
           A third of the apparent edge was the tick being in the wrong
           place. */
        const breakEven =
          z.provenance.realPricedBets > 0 && Number.isFinite(z.provenance.realBreakEven)
            ? z.provenance.realBreakEven
            : FLAT_110;
        const rate = hit.rate;
        const hasRate = Number.isFinite(rate);
        const above = hasRate && rate >= breakEven;
        const fillW = hasRate ? Math.min(100, rate * 100) : 0;
        const roi = zoneReturn(z);
        return (
          <div key={z.label} className={styles.zoneRow}>
            <div className={styles.zoneLabel}>
              <span className={styles.zoneName}>{z.label}</span>
              <span className={styles.zoneN}>
                {hit.wins}-{hit.losses}
                {hit.real ? " priced" : " graded"}
              </span>
            </div>
            <div className={styles.zoneBarTrack}>
              <div
                className={`${styles.zoneBarFill} ${above ? styles.zoneAbove : styles.zoneBelow}`}
                style={{ width: `${fillW}%` }}
              />
              <span
                className={styles.zoneBreakEven}
                style={{ left: `${breakEven * 100}%` }}
                aria-hidden
              />
            </div>
            <div
              className={`${styles.zoneRate} ${
                !hasRate ? styles.numFlat : above ? styles.numWin : styles.numLoss
              }`}
            >
              {hasRate ? `${(rate * 100).toFixed(1)}%` : "—"}
            </div>
            {/* 2026-07-30: a RETURN PER UNIT STAKED, not a unit total --
                see the block above zoneReturn(). (2026-07-28 note kept:
                this once printed z.unitsPL, the raw sum INCLUDING bets
                settled against a fabricated -110, so the table read
                +33.50u for a season the ROI panel one click away read
                as -1.03u. Same bets, opposite signs. The population fix
                stands; only the unit has changed.) */}
            <div className={`${styles.zonePL} ${roi != null && roi >= 0 ? styles.numWin : styles.numLoss}`}>
              {zoneReturnText(z)}
            </div>
            <div className={styles.zoneSub}>
              {z.provenance.realPricedBets > 0 ? (
                <>
                  Needs {(breakEven * 100).toFixed(1)}% to break even at the
                  prices actually paid.{" "}
                  {z.provenance.placeholderBets > 0 && (
                    <>
                      {z.provenance.realPricedBets} of {z.bets} graded bets had a
                      captured price; the other {z.provenance.placeholderBets}{" "}
                      settled against an assumed &minus;110 and are not counted
                      on this row.
                    </>
                  )}
                </>
              ) : (
                <>
                  No captured prices in this zone, so the mark sits at the
                  &minus;110 break-even of 52.4% and the units are
                  hypothetical.
                </>
              )}
            </div>
          </div>
        );
      })}
      <div className={styles.zoneFoot}>
        The vertical mark on each bar is that zone&rsquo;s own break-even rate
        &mdash; the average price actually paid, not a flat &minus;110. A bar
        that reaches past its mark made money; one short of it lost. The
        figure on the right is <b>return per unit staked</b> rather than a
        unit total: units from different dates are worth different money
        once the bank has moved, so adding them across a season is not a
        quantity. It assumes one unit a bet, which is what the ledger
        recorded for every row before 2026-07-28.
        {placeholderTotal > 0 && (
          <> {placeholderTotal} graded {placeholderTotal === 1 ? "bet" : "bets"} across
          all zones had no captured price and are excluded from both the rate
          and the units here.</>
        )}
      </div>
    </div>
  );
}

/* ------------- table row ------------- */

function DayRow({ day, maxAbs }: { day: DayRecord; maxAbs: number }) {
  const isWin = day.units > 0;
  const isLoss = day.units < 0;
  const fillPct = (Math.abs(day.units) / maxAbs) * 100;

  return (
    <div className={styles.row}>
      <div className={styles.dateCell}>
        <span className={styles.dateMain}>{formatDate(day.date)}</span>
      </div>
      <div className={`${styles.right} ${isWin ? styles.numWin : isLoss ? styles.numLoss : styles.numFlat}`}>
        {formatUnits(day.units)}
      </div>
      {/* A LEVEL, so no tone and no sign (2026-07-30). This column was
          cumulative units and was tone-coloured by whether that total
          was positive -- which meant the whole column turned green the
          moment the season went into profit and stayed green through
          every losing week inside it. */}
      <div className={`${styles.right} ${styles.numFlat}`}>
        {formatLevel(day.bank)}
      </div>
      <div className={styles.barCell}>
        <div className={styles.distBar}>
          <div
            className={`${styles.distFill} ${isWin ? styles.distFillWin : styles.distFillLoss}`}
            /* HALF-WIDTH PER SIDE (2026-07-30). This is a diverging bar
               centred at 50%, so each wing has 50% of the track to grow
               into -- but the fill was drawn at the FULL magnitude, so
               the biggest day rendered a bar from 50% to 150% and the
               track's `overflow: hidden` clipped it.

               The bug was not cosmetic: clipping caps every large day at
               the same visible length, so the worst night of the season
               and a night 40% smaller drew an identical bar. The column
               exists to compare magnitudes and it silently stopped being
               able to above the halfway mark. Ten of the visible rows
               were overflowing, the worst by 133px of a 265px track. */
            style={{
              width: `${fillPct / 2}%`,
              marginLeft: isWin ? "50%" : `${50 - fillPct / 2}%`,
            }}
          />
          <div className={styles.distMid} />
        </div>
      </div>
    </div>
  );
}

/* ------------- helpers ------------- */

/* formatUnits() and unitsText() DELETED 2026-07-30 -- both moved to
   lib/units.ts. They were private to this file, which is precisely why
   a season unit total could be printed on four surfaces here with
   nothing able to object. The shared one refuses a figure branded as
   summed-across-days, and refuses it at compile time. */

function formatDate(iso: string): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return "—";
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    weekday: "short",
    timeZone: "UTC",
  });
}

// tileTone() deleted 2026-07-29: its only caller was the pair of
// same-weight summary tiles the hero replaced. It returned a
// CARD-level class that coloured a descendant and painted an inset
// side stripe, so it was also the wrong tool for an inline figure.
// The hero uses .actualFig[data-tone] instead.
