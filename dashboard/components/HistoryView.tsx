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
import type { RecFile } from "@/lib/season-record";
import { replayWindow, isNum } from "@/lib/season-record";
import styles from "./HistoryView.module.css";

const WINDOWS: { key: RoiWindow; label: string }[] = [
  { key: "7d",     label: "Last 7 days" },
  { key: "30d",    label: "Last 30 days" },
  { key: "season", label: "Season" },
];

interface DayRecord {
  date: string;
  units: number;       // daily P&L
  cumulative: number;  // running total
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

  const money = useMemo(() => pickMoneySeries(data), [data]);
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

  /** date -> the SYSTEM's P&L for that day, rebased to the operator's
   *  real bank, so the daily ledger reconciles to the hero above it.
   *
   *  2026-07-29. The hero said +14.54u and the table below it ended at
   *  -4.41u with nothing saying they were different populations, so the
   *  table read as though the numbers were broken. (They were not --
   *  the running total checks out across every row.) The table now
   *  carries BOTH columns and each one sums to its own headline.
   *
   *  TWO TRAPS, both verified numerically against the live record
   *  rather than assumed:
   *
   *  1. `day.simPnl` is NOT usable here. It includes NRFI, which the
   *     hero excludes because NRFI is not bet. Over the 30-day window
   *     day.simPnl rebases to +10.42u while the hero shows +14.54u --
   *     the -4.12u gap is exactly the NRFI side. So the per-day figure
   *     is summed from the day's GAMES, YRFI only, matching
   *     replayWindow's own bucketing.
   *
   *  2. Rebasing must use ONE divisor -- the bank at window start --
   *     not each day's own bank. Scaling every day by the same constant
   *     is what makes the column sum to the hero; scaling each by its
   *     own bank would not. */
  const sysDaily = useMemo(() => {
    const out = new Map<string, number>();
    if (!sysSide || !sysWindow || !isNum(sysWindow.bankStart) || sysWindow.bankStart <= 0) {
      return out;
    }
    const scale = (sysBank || 100) / sysWindow.bankStart;
    for (const d of sysSide.days) {
      if (d.date < data.startDate || d.date > data.endDate) continue;
      let yrfi = 0;
      for (const g of d.games) {
        if (g.record.action !== "BET") continue;
        if (g.side === "NRFI") continue;      // tracked, never bet
        yrfi += g.record.pnl ?? 0;
      }
      out.set(d.date, yrfi * scale);
    }
    return out;
  }, [sysSide, sysWindow, sysBank, data.startDate, data.endDate]);

  // Derive per-day records from the REAL-PRICED cumulative series.
  // Daily = cum[i] - cum[i-1].
  const days = useMemo<DayRecord[]>(() => {
    const out: DayRecord[] = [];
    let prev = 0;
    for (const row of money.points) {
      out.push({
        date:       row.date,
        units:      row.units - prev,
        cumulative: row.units,
      });
      prev = row.units;
    }
    return out;
  }, [money.points]);

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
        `${z.label} ${unitsText(z.provenance.realPricedPL)} over ` +
        `${z.provenance.realPricedBets} ` +
        `${z.provenance.realPricedBets === 1 ? "bet" : "bets"}`,
    );
    if (parts.length === 0) return undefined;
    return (
      "Split by side, counting only bets that had a real captured price: " +
      `${parts.join(" · ")}.`
    );
  }, [data.betZones, data.passZones]);

  const totalUnits = days.length ? days[days.length - 1].cumulative : 0;
  const totalDays  = days.length;
  const bestDay    = days.length ? Math.max(...days.map((d) => d.units)) : 0;
  const worstDay   = days.length ? Math.min(...days.map((d) => d.units)) : 0;

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
            <div className={styles.heroFig}>
              {formatUnits(
                isNum(sysWindow.bankStart) && sysWindow.bankStart > 0
                  ? (sysWindow.yrfi.pnl / sysWindow.bankStart) * (sysBank || 100)
                  : sysWindow.yrfi.pnl,
              )}
            </div>
            <div className={styles.heroSub}>
              {sysWindow.yrfi.wins}-{sysWindow.yrfi.bets - sysWindow.yrfi.wins} over{" "}
              {sysWindow.yrfi.bets} {sysWindow.yrfi.bets === 1 ? "bet" : "bets"} ·{" "}
              {sysWindow.from} → {sysWindow.to}
            </div>
            {/* The unlevered twin, right under the levered one. Over 30
                days these read +14.54u and +0.89u: same bets, one of
                them multiplied by compounding. Kept adjacent so the
                difference is impossible to miss. */}
            <div className={styles.heroFlat}>
              <span className={styles.heroFlatFig}>
                {formatUnits(sysWindow.flatPnl)}
              </span>
              <span>unlevered, at a flat 1u on the same bets</span>
            </div>
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

        {/* WHAT ACTUALLY HAPPENED -- subordinate by design. Real money,
            so it keeps its tone colour while the simulated hero above
            stays neutral ink, per the colour law. */}
        <div className={styles.actual}>
          <span className={styles.actualLabel}>What actually happened</span>
          {/* data-tone, NOT tileTone(): that helper returns a CARD-level
              class which colours a descendant `.tileBig` and paints an
              inset side stripe. Hung on an inline <span> it coloured
              nothing and drew a stray 3px bar. */}
          <span
            className={styles.actualFig}
            data-tone={totalUnits > 0.005 ? "win" : totalUnits < -0.005 ? "loss" : "flat"}
          >
            {formatUnits(totalUnits)}
          </span>
          <span className={styles.actualSub}>
            across {totalDays} {totalDays === 1 ? "day" : "days"} ·{" "}
            {money.isReal ? (
              <>
                {priceSplit.real} graded{" "}
                {priceSplit.real === 1 ? "bet" : "bets"} at a real captured
                DraftKings price
                {priceSplit.assumed > 0 && (
                  <>
                    {"; "}
                    {priceSplit.assumed} more settled against an assumed
                    &minus;110 and are left out
                  </>
                )}
              </>
            ) : (
              // Reachable only by a browser holding an /api/roi payload
              // cached from before 2026-07-29. It used to say "reload the
              // page to pick up the real-priced figure" -- but the
              // producer did not exist, so this branch was permanent and
              // the advice could never work.
              <>
                inflated: includes bets settled against an assumed
                &minus;110 rather than a price you paid
              </>
            )}
          </span>
        </div>
      </section>

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
      <UnderwaterChart
        series={money.points}
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
          <div className={styles.theadRow}>
            <div>Date</div>
            <div className={styles.right}>System</div>
            <div className={styles.right}>You</div>
            <div className={styles.right}>Your total</div>
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
                sysUnits={sysDaily.get(d.date)}
                maxAbs={Math.max(Math.abs(bestDay), Math.abs(worstDay), 0.01)}
              />
            ))
          )}
        </div>
        <div className={styles.tableFoot}>
          <b>System</b> is what today&apos;s rules would have staked that day,
          simulated, rebased to your {sysBank ?? 100}u bank — it sums to the
          figure at the top of this page. <b>You</b> is what actually moved.
          They differ because most of these nights were bet under rules that
          have since changed.
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
  peak: number;            // ATH cumulative value
  peakDate: string | null; // ISO date of the ATH
  maxDrawdown: number;     // deepest peak-to-trough draw (positive number)
  maxDrawdownPct: number;  // maxDrawdown / peak * 100  (0 when peak <= 0)
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
    return { peak: 0, peakDate: null, maxDrawdown: 0, maxDrawdownPct: 0, totalDays: 0 };
  }

  // Running peak per day -- once a day's cumulative exceeds the prior
  // peak, that's a new ATH.
  let runningPeak = -Infinity;
  let runningPeakDate: string | null = null;
  let maxDD = 0;          // largest peak-to-trough draw seen

  for (const d of days) {
    if (d.cumulative > runningPeak) {
      runningPeak = d.cumulative;
      runningPeakDate = d.date;
    }
    const dd = runningPeak - d.cumulative;
    if (dd > maxDD) maxDD = dd;
  }

  return {
    peak:           runningPeak === -Infinity ? 0 : runningPeak,
    peakDate:       runningPeakDate,
    maxDrawdown:    maxDD,
    maxDrawdownPct: runningPeak > 0 ? (maxDD / runningPeak) * 100 : 0,
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
  const rawByDate = new Map(rawSeries.map((p) => [p.date, p.units]));
  const rawValues = days
    .map((d) => rawByDate.get(d.date))
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));

  // Y range pinned to include 0 so "where the bankroll started" is
  // always visible, even if we've been profitable the whole window.
  const cumMax = Math.max(0, stats.peak, days[days.length - 1].cumulative, ...rawValues);
  const cumMin = Math.min(
    0,
    ...days.map((d) => d.cumulative),
    ...rawValues,
  );
  const cumRange = cumMax - cumMin || 1;

  const stepX = innerW / Math.max(days.length, 1);
  const xFor = (i: number) => padL + (i + 0.5) * stepX;
  const yFor = (v: number) => padT + innerH - ((v - cumMin) / cumRange) * innerH;
  const yZero = yFor(0);

  // Build the equity line path
  const linePath = days
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xFor(i).toFixed(1)} ${yFor(d.cumulative).toFixed(1)}`)
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
    days.map((d, i) => `L ${xFor(i).toFixed(1)} ${yFor(d.cumulative).toFixed(1)}`).join(" ") +
    ` L ${xFor(days.length - 1).toFixed(1)} ${yZero.toFixed(1)} Z`;

  // Drawdown shading: for each point, draw a thin segment from the
  // running peak DOWN to the current cumulative.  We render this as
  // a single polygon: top edge follows the running-peak watermark,
  // bottom edge follows the equity line.  Only fill where peak >
  // current (i.e., we're in drawdown).
  let runningPeak = -Infinity;
  const peakLine: { x: number; y: number; v: number }[] = [];
  for (let i = 0; i < days.length; i++) {
    if (days[i].cumulative > runningPeak) runningPeak = days[i].cumulative;
    peakLine.push({ x: xFor(i), y: yFor(runningPeak), v: runningPeak });
  }
  // Shade polygons -- one per contiguous drawdown segment so we don't
  // shade the regions where equity == peak (no DD there).
  const drawdownPolys: { d: string }[] = [];
  let segStart = -1;
  for (let i = 0; i < days.length; i++) {
    const inDD = days[i].cumulative < peakLine[i].v;
    if (inDD && segStart < 0) segStart = i;
    if ((!inDD || i === days.length - 1) && segStart >= 0) {
      const end = inDD ? i : i - 1;
      const top = peakLine.slice(segStart, end + 1)
        .map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" L ");
      const bot = days.slice(segStart, end + 1).reverse()
        .map((d, k) => {
          const idx = end - k;
          return `${xFor(idx).toFixed(1)} ${yFor(d.cumulative).toFixed(1)}`;
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
                {t.v >= 0 ? "+" : ""}{t.v.toFixed(0)}u
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
              cy={yFor(days[lastIdx].cumulative)}
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
          <div className={styles.equityStatLabel}>Window P&amp;L</div>
          <div
            className={styles.equityStatBig}
            data-tone={days[lastIdx].cumulative >= 0 ? "pos" : "neg"}
          >
            {formatUnits(days[lastIdx].cumulative)}
          </div>
          <div className={styles.equityStatSub}>
            {/* 2026-07-28: this cell was labelled "Bankroll" and printed
                cumulative window P&L.  The bankroll is 100u plus or minus
                this figure -- two different quantities under one label. */}
            over {stats.totalDays} {stats.totalDays === 1 ? "day" : "days"}
          </div>
        </div>
        <div className={styles.equityStatCell}>
          <div className={styles.equityStatLabel}>All-time high</div>
          {/* 2026-07-28: data-tone was hardcoded "pos", so on a losing
              window this rendered a NEGATIVE all-time high in the profit
              colour. */}
          <div
            className={styles.equityStatBig}
            data-tone={stats.peak > 0 ? "pos" : stats.peak < 0 ? "neg" : "neutral"}
          >
            {formatUnits(stats.peak)}
          </div>
          <div className={styles.equityStatSub}>
            {stats.peakDate ? `on ${stats.peakDate.slice(5)}` : "—"}
          </div>
        </div>
        <div className={styles.equityStatCell}>
          <div className={styles.equityStatLabel}>Max drawdown</div>
          <div
            className={styles.equityStatBig}
            data-tone={stats.maxDrawdown > 0 ? "neg" : "neutral"}
          >
            {stats.maxDrawdown > 0 ? `−${stats.maxDrawdown.toFixed(2)}u` : "0.00u"}
          </div>
          <div className={styles.equityStatSub}>
            {/* 2026-07-28: maxDrawdownPct guards on peak > 0 but the "no
                drawdown" copy did not, so a window that never went positive
                printed "no drawdown" underneath a -13.19u drawdown. */}
            {stats.maxDrawdown <= 0
              ? "never below the high"
              : stats.peak > 0
                ? `${stats.maxDrawdownPct.toFixed(1)}% of peak`
                : "the window never went positive, so there is no peak to measure against"}
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

/** P&L over bets that had a REAL captured DraftKings price.
 *
 *  Falls back to the raw column only when provenance is unknown (nothing
 *  classified), which mirrors RoiPanel.realPL so the two screens cannot
 *  print different signs for the same season again. */
function realZonePL(z: import("@/lib/roi").ZoneRoi): number {
  const pr = z.provenance;
  const known = pr.realPricedBets + pr.placeholderBets;
  return known > 0 ? pr.realPricedPL : z.unitsPL;
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
        const pl = realZonePL(z);
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
            {/* 2026-07-28 AUDIT FIX: this printed z.unitsPL -- the raw sum
                INCLUDING bets settled against a fabricated -110 because no
                DraftKings price was ever captured. That made this table read
                +33.50u for the season while the ROI panel one click away read
                -1.03u for the same bets. Opposite signs, same season. Show the
                real-priced figure, matching RoiPanel. */}
            <div className={`${styles.zonePL} ${pl >= 0 ? styles.numWin : styles.numLoss}`}>
              {formatUnits(pl)}
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
        that reaches past its mark made money; one short of it lost.
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

function DayRow({
  day, sysUnits, maxAbs,
}: { day: DayRecord; sysUnits?: number; maxAbs: number }) {
  const isWin = day.units > 0;
  const isLoss = day.units < 0;
  const fillPct = (Math.abs(day.units) / maxAbs) * 100;

  return (
    <div className={styles.row}>
      <div className={styles.dateCell}>
        <span className={styles.dateMain}>{formatDate(day.date)}</span>
      </div>
      {/* SYSTEM -- simulated, so NEUTRAL ink whatever its sign.
          globals.css: "SIMULATED FIGURES ARE NEVER TONE-COLOURED."
          The column beside it is real money and does carry tone; the
          contrast between them is the point. "—" means the replay has
          no entry for this date, which is different from a 0.00u day
          where it looked and chose not to bet. */}
      <div className={`${styles.right} ${styles.sysCell}`}>
        {sysUnits == null ? "—" : formatUnits(sysUnits)}
      </div>
      <div className={`${styles.right} ${isWin ? styles.numWin : isLoss ? styles.numLoss : styles.numFlat}`}>
        {formatUnits(day.units)}
      </div>
      <div
        className={`${styles.right} ${
          day.cumulative > 0 ? styles.numWin : day.cumulative < 0 ? styles.numLoss : styles.numFlat
        }`}
      >
        {formatUnits(day.cumulative)}
      </div>
      <div className={styles.barCell}>
        <div className={styles.distBar}>
          <div
            className={`${styles.distFill} ${isWin ? styles.distFillWin : styles.distFillLoss}`}
            style={{
              width: `${fillPct}%`,
              marginLeft: isWin ? "50%" : `${50 - fillPct}%`,
            }}
          />
          <div className={styles.distMid} />
        </div>
      </div>
    </div>
  );
}

/* ------------- helpers ------------- */

function formatUnits(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "0.00u";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}u`;
}

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

/** Units as plain text for a prose sentence.  Uses U+2212 MINUS rather
 *  than a hyphen so the figure reads as a negative number, not a dash.
 *  Em dash on a missing value -- never "0.00u", which the operator would
 *  read as "nothing happened". */
function unitsText(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "0.00u";
  return n > 0 ? `+${n.toFixed(2)}u` : `−${Math.abs(n).toFixed(2)}u`;
}

// tileTone() deleted 2026-07-29: its only caller was the pair of
// same-weight summary tiles the hero replaced. It returned a
// CARD-level class that coloured a descendant and painted an inset
// side stripe, so it was also the wrong tool for an inline figure.
// The hero uses .actualFig[data-tone] instead.
