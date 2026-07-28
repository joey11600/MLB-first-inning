"use client";

import { useEffect, useMemo, useState } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import type { LeanPaperTrade, RoiResponse, RoiWindow, ZoneRoi, ZoneProvenance } from "@/lib/roi";
import { aggregateTodayRoi, aggregateTodayClvMeasured } from "@/lib/roi-today";
import type { TodayClvMeasured } from "@/lib/roi-today";
import type { RecFile, RecSide } from "@/lib/season-record";
import { isNum, replayWindow } from "@/lib/season-record";
import { nightFromRecord, nightFromBoard, fmtU } from "@/lib/reconcile";
import { DayReconcile } from "./DayReconcile";
import styles from "./RoiPanel.module.css";

/* ============================================================
   Performance panel.

   REBUILT 2026-07-28. The operator's complaint was that one night
   appeared three different ways on this one screen -- "6 STRONG YRFI"
   in the ticker, "4 graded bets -0.33" here, and "1 bet -11.15u" in
   the season record -- with nothing saying why. Three components each
   derived their own count from a different source.

   The structural answers:

     * REAL MONEY AND SIMULATED MONEY ARE DIFFERENT SURFACES. The
       ledger is a raised card with a tone rail. The replay is a
       recessed, hatched card whose figures can never take a tone
       colour (see .simCard in the stylesheet). Previously the two
       largest numbers on the panel -- +365.10u and +130.18u -- were
       simulated bankrolls presented in the same visual language as
       real P&L, sitting directly above a real ledger reading -0.33.

     * THE HEADLINE IS FLAT PROFIT. Compounding quarter-Kelly on an
       imaginary 100u bank turns a +34u edge into a +880u "profit"
       that was never staked. That figure still renders -- the
       operator has a history of things appearing to vanish -- but as
       one sentence, tagged SIMULATED, at meta size.

     * EVERY COUNT COMES FROM lib/reconcile. Not recomputed here.

   The per-date drill-down lives in DayReconcile, which is where the
   three numbers are actually reconciled game by game.
   ============================================================ */

const WINDOWS: { key: RoiWindow; label: string }[] = [
  { key: "today",  label: "Today"    },
  { key: "7d",     label: "Last 7d"  },
  { key: "30d",    label: "Last 30d" },
  { key: "season", label: "Season"   },
];

interface RoiPanelProps {
  initialDate: string;
  rows:        BoardRow[];
  details:     Record<string, GameDetail>;
  /** Fetched once by DashboardShell and shared with the board's stake
   *  chips, so the two surfaces can never quote different stakes. */
  seasonRecord: RecFile | null;
}

export function RoiPanel({ initialDate, rows, details, seasonRecord }: RoiPanelProps) {
  const [window, setWindow]   = useState<RoiWindow>("today");
  const [data, setData]       = useState<RoiResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // Closing-line value, measured properly: a bet only counts when the
  // opening and the taken price actually DIFFER. See lib/roi-today.
  const clv = useMemo(
    () => aggregateTodayClvMeasured(rows, details),
    [rows, details],
  );

  const todayData = useMemo<RoiResponse | null>(() => {
    if (window !== "today" || !initialDate) return null;
    return aggregateTodayRoi(rows, details, initialDate);
  }, [window, rows, details, initialDate]);

  useEffect(() => {
    if (window === "today") {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const url = `/api/roi?window=${window}${initialDate ? `&date=${initialDate}` : ""}`;
    fetch(url, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: RoiResponse | null) => {
        if (!cancelled) { setData(j); setLoading(false); }
      })
      .catch(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [window, initialDate]);

  // THE system record arrives as a prop -- DashboardShell fetches it once
  // and shares it with the board's stake chips. A null side is rendered
  // as a per-side notice, never by hiding the whole card.
  const view = window === "today" ? todayData : data;

  // The reconciliation source for the selected date. Prefer the REAL
  // record (real captured prices); fall back to PROJECTED for dates
  // before real pricing began.
  // Operator wants every date back to opening day. REAL only covers
  // 2026-05-07 onward (36 April dates are projected-only), so fall
  // through rather than showing an empty day for the first month.
  const realSide = seasonRecord?.real ?? null;
  const projSide = seasonRecord?.projected ?? null;
  const realDay  = realSide?.days.find((d) => d.date === initialDate) ?? null;
  const projDay  = projSide?.days.find((d) => d.date === initialDate) ?? null;
  const recDay   = realDay ?? projDay;
  const recSide  = realDay ? realSide : projDay ? projSide : (realSide ?? projSide);
  const fromRec  = nightFromRecord(recDay);
  const tonight  = useMemo(
    () => nightFromBoard(rows, details, initialDate),
    [rows, details, initialDate],
  );
  // A graded date uses the record's reconciliation; the live slate uses
  // the board, whose replay fields are null (never 0).
  const night = fromRec ?? tonight;

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <div className={styles.headLeft}>
          <span className={styles.eyebrow}>Performance</span>
          {view && (
            <span className={styles.range}>
              {window === "today"
                ? <>Tonight&rsquo;s slate</>
                : <>{view.startDate} → {view.endDate}</>}
              {view.daysIncluded > 0 && window !== "today" && (
                <>
                  {" · "}
                  <span className={styles.rangeStrong}>{view.gradedPicks} graded picks</span>
                  {" across "}{view.daysIncluded}{" "}
                  {view.daysIncluded === 1 ? "day" : "days"}
                  {view.totalPicks > view.gradedPicks && (
                    <span className={styles.rangePending}>
                      {" "}({view.totalPicks - view.gradedPicks} pending)
                    </span>
                  )}
                </>
              )}
              {window === "today" && view.totalPicks > 0 && (
                <>
                  {" · "}
                  <span className={styles.rangeStrong}>{view.gradedPicks} graded</span>
                  {view.totalPicks > view.gradedPicks && (
                    <span className={styles.rangePending}>
                      {" "}({view.totalPicks - view.gradedPicks} pending)
                    </span>
                  )}
                </>
              )}
            </span>
          )}
        </div>
        <div className={styles.windowToggle} role="tablist" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.key}
              role="tab"
              aria-selected={window === w.key}
              className={`${styles.windowBtn} ${window === w.key ? styles.windowBtnActive : ""}`}
              onClick={() => setWindow(w.key)}
              type="button"
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      <div className={`${styles.body} ${loading ? styles.loading : ""}`}>
        {/* Real money leads. */}
        <TotalCard total={view?.total} window={window} clv={clv} />
        {/* ...immediately followed by what the CURRENT model would have
            done over the SAME window. Without this, selecting "Last 7d"
            showed the old system's real result with nothing anywhere
            saying what the new one would have returned. */}
        <WindowReplayCard
          side={seasonRecord?.real ?? seasonRecord?.projected ?? null}
          startDate={view?.startDate}
          endDate={view?.endDate}
          window={window}
        />
        <MigrationNote />

        <div className={styles.zoneGrid}>
          {(view?.betZones ?? []).map((z) => (
            <ZoneCard key={z.label} zone={z} />
          ))}
          {view && view.betZones.length === 0 && (
            <div className={styles.emptyZone}>
              {window === "today"
                ? "No graded bets tonight yet."
                : "No graded bets in this window yet."}
            </div>
          )}
        </div>

        {/* Then the night-by-night reconciliation -- the answer to
            "where did my bets go". */}
        <DayReconcile
          day={recDay}
          tonight={night}
          selectedDate={initialDate}
          sideFrom={recSide?.from}
          sideTo={recSide?.to}
        />

        {/* Then, clearly marked as simulation, the model replay. */}
        {seasonRecord && <SeasonRecordCard rec={seasonRecord} />}

        {view?.leanPaperTrade && view.leanPaperTrade.picks > 0 && (
          <LeanBlock paper={view.leanPaperTrade} />
        )}

        {view && view.passZones.length > 0 && (
          <div className={styles.passRow}>
            <span className={styles.passEyebrow}>No-bet calls</span>
            {view.passZones.map((z) => (
              <span key={z.label} className={styles.passChip}>
                <span className={styles.passChipLabel}>{z.label}</span>
                <span className={styles.passChipCount}>{z.picks}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- */

/** The replay, restricted to the window the toggle is showing.
 *
 *  Deliberately sits directly under the ledger so the comparison is
 *  unavoidable: same days, same slate, two different systems and two
 *  different stake sizes. It is simulated money, so it wears the
 *  recessed treatment and never a tone colour. */
function WindowReplayCard({
  side, startDate, endDate, window: win,
}: {
  side: RecSide | null;
  startDate: string | undefined;
  endDate: string | undefined;
  window: RoiWindow;
}) {
  const w = replayWindow(side, startDate, endDate);
  if (!w) return null;
  return (
    <div className={styles.simCard}>
      <div className={styles.recHead}>
        <span className={styles.eyebrow}>
          {win === "today" ? "Model replay · tonight" : "Model replay · same window"}
        </span>
        <span className={styles.tag}>Simulated</span>
      </div>
      <div className={styles.windowRow}>
        <span className={styles.figLead}>{fmtU(w.pnl)}</span>
        <span className={styles.meta}>
          {`${w.bets} ${w.bets === 1 ? "bet" : "bets"} · ${w.wins}-${w.losses} · ¼-Kelly`}
          {isNum(w.bankStart) && isNum(w.bankEnd)
            ? ` · bank ${w.bankStart.toFixed(2)}u → ${w.bankEnd.toFixed(2)}u`
            : ""}
          {` · ${w.from} → ${w.to}`}
        </span>
      </div>
      <span className={styles.meta}>
        Kelly scales the stake to the bankroll, so a losing stretch costs
        far more in units than flat betting would — and a winning one
        returns far more. This is the same slate as the ledger above,
        sized differently.
      </span>
    </div>
  );
}

/** One-week note explaining where the giant bankroll number went.
 *
 *  The operator has watched a compounded figure (+365.10u) as though it
 *  were his season. Replacing the headline with flat profit moves it to
 *  +34.66u. His incident history is entirely about things appearing to
 *  disappear, so the change is announced rather than discovered. */
function MigrationNote() {
  const [gone, setGone] = useState(false);
  if (gone) return null;
  return (
    <div className={styles.migrationNote}>
      <span className={styles.copy}>
        The big bankroll number moved. It is now one sentence inside{" "}
        <b>Model replay</b>, marked SIMULATED — it was a compounding
        back-test on an imaginary 100u bank, not your ledger. Nothing was
        deleted.
      </span>
      <button type="button" className={styles.noteDismiss} onClick={() => setGone(true)}>
        Dismiss
      </button>
    </div>
  );
}

function TotalCard({
  total, window, clv,
}: {
  total:  ZoneRoi | undefined;
  window: RoiWindow;
  clv:    TodayClvMeasured | null;
}) {
  const shownPL = total ? realPL(total) : 0;
  const tone: "win" | "loss" | "neutral" =
    !total || total.bets === 0 ? "neutral"
    : shownPL > 0.05 ? "win" : shownPL < -0.05 ? "loss" : "neutral";

  const prov = total?.provenance;
  const known = prov ? prov.realPricedBets + prov.placeholderBets : 0;

  const sub = !total || total.bets === 0
    ? "No bets settled in this window yet."
    : `${total.bets} settled bets${window === "today" ? " tonight" : ""} · ` +
      `${total.wins}W-${total.losses}L · at the DraftKings prices you got`;

  return (
    <div className={styles.moneyCard} data-tone={tone}>
      <div className={styles.totalLeft}>
        <span className={styles.eyebrow}>
          {window === "today"
            ? "Ledger · real money · all graded bets tonight"
            : "Ledger · real money · bets you actually placed"}
        </span>
        <span className={styles.figHero}>
          {total && total.bets > 0 ? fmtU(shownPL) : "—"}
        </span>
        <span className={styles.meta}>{sub}</span>
        {prov && known > 0 && prov.placeholderBets > 0 && (
          <span className={styles.provNote}>
            <span className={styles.provDot} aria-hidden />
            {`${prov.placeholderBets} of ${known} bets had no captured DraftKings price and settled at a placeholder −110. `}
            {`Counting those reads ${fmtU(total!.unitsPL)}.`}
          </span>
        )}
      </div>
      <div className={styles.totalRight}>
        <Stat
          label="Settled W-L"
          value={total && total.bets > 0 ? `${total.wins}-${total.losses}` : "0-0"}
        />
        <Stat
          label="Hit rate"
          value={total && !Number.isNaN(total.hitRate) && total.bets > 0
            ? pctText(total.hitRate) : "—"}
        />
        {window === "today"
          ? <ClvStat clv={clv} />
          : <Stat
              label="vs break-even"
              value={total && !Number.isNaN(total.edgeVsBreakEven)
                ? signedPctText(total.edgeVsBreakEven) : "—"}
            />}
      </div>
    </div>
  );
}

/** Closing-line value, or an honest statement that there isn't one.
 *
 *  This used to render "+0.00pp" whenever no movement was observed,
 *  which reads as "we measured zero" when the truth is "there was
 *  nothing to measure": once a bet is placed the price is frozen
 *  (T2.23), so for most rows the opening and taken prices are the same
 *  recorded number. A measured zero and an unmeasurable quantity are
 *  different claims. */
function ClvStat({ clv }: { clv: TodayClvMeasured | null }) {
  // Narrow to a local so TypeScript can prove avgPp is a number in the
  // measurable branch -- the library types avgPp as null EXACTLY when
  // measured === 0, which is what makes "+0.00pp" unrepresentable.
  const avg = clv && clv.measured > 0 ? clv.avgPp : null;
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>Closing-line value</span>
      <span className={`${styles.figStat} ${avg == null ? styles.statMuted : ""}`}>
        {avg == null ? "Not measurable" : signedPpText(avg / 100)}
      </span>
      <span className={styles.statCaption}>
        {avg != null && clv
          ? `across ${clv.measured} of ${clv.placed} placed bets`
          : `We bet the first price we see and lock it, so there is no closing price to compare against.` +
            (clv ? ` Tonight 0 of ${clv.placed} placed bets saw the line move.` : "")}
      </span>
    </div>
  );
}

/* ---------------------------------------------------------------- */

/** The model replay: what the CURRENTLY DEPLOYED model would have done.
 *
 *  Everything in here is simulated. The card is recessed and hatched,
 *  and .simCard forces every figure to --foreground so a simulated
 *  number can never render in the same peach as real profit. */
function SeasonRecordCard({ rec }: { rec: RecFile }) {
  return (
    <div className={styles.simCard}>
      <div className={styles.recHead}>
        <span className={styles.eyebrow}>Model replay</span>
        <span className={styles.tag}>Simulated</span>
      </div>
      <p className={styles.meta}>{rec.headlineMethod}</p>
      <div className={styles.recGrid}>
        <RecordColumn side={rec.real} which="real" />
        <RecordColumn side={rec.projected} which="projected" />
      </div>
      <p className={`${styles.meta} ${styles.simNote}`}>{rec.caveat}</p>
    </div>
  );
}

function RecordColumn({ side, which }: { side: RecSide | null; which: "real" | "projected" }) {
  const eyebrow = which === "real"
    ? "Real prices · ¼-Kelly compounded"
    : "Whole season · ¼-Kelly compounded";
  if (!side) {
    return (
      <div className={styles.recCol}>
        <span className={styles.eyebrow}>{eyebrow}</span>
        <span className={styles.meta}>
          Model replay unavailable — the export ran but produced no staked
          bets for this side.
        </span>
      </div>
    );
  }
  const k = side.sim;
  // 2026-07-28 (operator): the system stakes by quarter-Kelly now, so the
  // record is read in Kelly units. Flat 1u stays on screen as the
  // un-leveraged reference -- it is what the edge is worth before any
  // sizing decision -- but it is no longer the headline.
  const tone: "win" | "loss" | "neutral" =
    !isNum(k?.profit) ? "neutral" : k.profit > 0.05 ? "win" : k.profit < -0.05 ? "loss" : "neutral";
  return (
    <div className={styles.recCol} data-tone={tone}>
      <span className={styles.eyebrow}>{eyebrow}</span>
      <span className={`${styles.figLead} ${styles.simFig}`}>
        {isNum(k?.profit) ? fmtU(k.profit) : "—"}
      </span>
      <span className={styles.meta}>
        {isNum(k?.finalBank)
          ? `bank ${k.startBank.toFixed(0)}u → ${k.finalBank.toFixed(2)}u · `
          : ""}
        {`${side.bets} bets · ${side.wins}-${side.losses} · `}
        {isNum(side.hitRate) ? `${(100 * side.hitRate).toFixed(1)}% hit` : ""}
      </span>
      {/* What compounding actually asks of you. An average hides it: on
          the projected path the bank grows ~10x, so the 10%-per-bet cap
          puts late stakes near 80u. */}
      {isNum(k?.medianStake) && (
        <span className={styles.meta}>
          {`typical bet ${k.medianStake.toFixed(2)}u · biggest ${(k.largestStake ?? 0).toFixed(2)}u · `}
          {`deepest drawdown ${k.maxDrawdownPct.toFixed(1)}%`}
        </span>
      )}
      <span className={styles.meta}>
        {side.from} → {side.to}
        {side.priceFill != null
          ? ` · missing prices filled at ${side.priceFill} (${side.assumedBets} of ${side.bets})`
          : " · real captured prices only"}
        {isNum(side.selectedBets) && (side.droppedZeroStake ?? 0) > 0 && (
          <> · staked {side.bets} of {side.selectedBets} qualifying ({side.droppedZeroStake} sized to zero)</>
        )}
      </span>
      {side.floor && (
        <div className={styles.floorBlock}>
          <span className={styles.eyebrow}>No-hindsight check</span>
          <span className={styles.meta}>
            {`${fmtU(side.floor.sim.profit)} over ${side.floor.bets} bets `}
            {`(${side.floor.wins}-${side.floor.losses}), same ¼-Kelly staking.`}
          </span>
        </div>
      )}
    </div>
  );
}

/** LEAN picks: model calls that were never bet. */
function LeanBlock({ paper }: { paper: LeanPaperTrade }) {
  return (
    <div className={styles.simCard}>
      <div className={styles.recHead}>
        <span className={styles.eyebrow}>Lean picks · never bet</span>
        <span className={styles.tag}>Not bet</span>
      </div>
      <span className={styles.figLead}>
        {paper.bets > 0 ? fmtU(paper.paperPL) : "—"}
      </span>
      <span className={styles.meta}>
        {paper.bets > 0
          ? `hypothetical flat −110 across ${paper.bets} graded LEAN (${paper.wins}W-${paper.losses}L)`
          : `${paper.picks} LEAN picks not graded yet — nothing to evaluate.`}
      </span>
    </div>
  );
}

function ZoneCard({ zone }: { zone: ZoneRoi }) {
  const tone = zoneTone(zone);
  const sideTone = zone.side === "NRFI" ? "nrfi" : zone.side === "YRFI" ? "yrfi" : "neutral";
  return (
    <div className={`${styles.zoneCard} ${styles[`zone_${sideTone}`]} ${styles[`tone_${tone}`]}`}>
      <header className={styles.zoneHead}>
        <span className={styles.zoneLabel}>{zone.label}</span>
        <span className={styles.zoneCount}>
          {zone.bets > 0 ? `${zone.wins}-${zone.losses}` : "—"}
        </span>
      </header>
      <div className={styles.zoneUnits}>
        {zone.bets > 0 ? fmtU(realPL(zone)) : "—"}
        <span className={styles.zoneUnitsLabel}>units</span>
      </div>
      <div className={styles.zoneSub}>
        {zone.bets > 0 ? (
          <>
            <span>{pctText(zone.hitRate)} hit</span>
            <span className={styles.zoneSep}>·</span>
            <span className={styles[`edge_${tone}`]}>
              {signedPctText(zone.edgeVsBreakEven)} vs break-even
            </span>
          </>
        ) : (
          <span className={styles.zoneSubMuted}>{zone.picks} picks, none graded yet</span>
        )}
      </div>
      <div className={styles.zoneBar}>
        <span
          className={styles.zoneBarFill}
          style={{ width: `${barWidthPct(zone.hitRate)}%` }}
          aria-hidden
        />
        <span className={styles.zoneBarBE} aria-hidden />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.figStat}>{value}</span>
    </div>
  );
}

// ---------- helpers ----------

/** The P&L a zone earned at prices we actually observed. */
function realPL(z: ZoneRoi): number {
  const p = z.provenance;
  const known = p.realPricedBets + p.placeholderBets;
  return known > 0 ? p.realPricedPL : z.unitsPL;
}

/** Tone follows realPL -- the number the card PRINTS. It used to key off
 *  z.unitsPL, the placeholder-inflated raw sum, so a zone could print a
 *  loss in the colour of a win. */
function zoneTone(z: ZoneRoi): "win" | "loss" | "neutral" {
  if (z.bets === 0) return "neutral";
  const v = realPL(z);
  if (v > 0.05) return "win";
  if (v < -0.05) return "loss";
  return "neutral";
}

function pctText(p: number): string {
  if (Number.isNaN(p)) return "—";
  return `${(p * 100).toFixed(1)}%`;
}

function signedPctText(p: number): string {
  if (Number.isNaN(p)) return "—";
  return `${p >= 0 ? "+" : ""}${(p * 100).toFixed(1)}pp`;
}

function signedPpText(p: number): string {
  if (!Number.isFinite(p)) return "—";
  return `${p >= 0 ? "+" : ""}${(p * 100).toFixed(2)}pp`;
}

function barWidthPct(hitRate: number): number {
  if (Number.isNaN(hitRate)) return 0;
  return Math.max(0, Math.min(100, hitRate * 100));
}

export type { ZoneProvenance };
