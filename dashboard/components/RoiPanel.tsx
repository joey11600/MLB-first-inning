"use client";

import { useEffect, useMemo, useState } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import type { RoiResponse, RoiWindow, ZoneRoi, ZoneProvenance } from "@/lib/roi";
import { aggregateTodayRoi } from "@/lib/roi-today";
import type { RecFile, RecSide } from "@/lib/season-record";
import { isNum, replayWindow } from "@/lib/season-record";
import type { NightCounts } from "@/lib/reconcile";
import { nightFromRecord, nightFromBoard, fmtU } from "@/lib/reconcile";
import { DayReconcile } from "./DayReconcile";
import styles from "./RoiPanel.module.css";

/* ============================================================
   Performance panel.  ZONE 2 (how am I doing) + ZONE 3 (why).

   REBUILT 2026-07-28, cut again the same day.  The operator asked for
   the dashboard to be recoloured and simplified; this panel is where
   most of the duplication lived.  Eight blocks are now four:

     1. SystemCard      the current model, quarter-Kelly, one headline
     2. .ledgerBlock    the superseded flat-1u ledger, two rows
     3. the LEAN line   a count, with no money on it
     4. DayReconcile    the night game by game (Zone 3, "why")

   WHAT WAS REMOVED AND WHERE ITS NUMBERS WENT — every deletion here
   was a second rendering of a fact already on the page:

     * LegacyLedgerLine  summed only the STRONG zones, i.e. exactly the
                         cards printed below it.  Its wording is now the
                         ledger block's eyebrow.
     * the 4 ZoneCards   the two STRONG ones became the two .ledgerRow
                         rows (every figure kept).  The two LEAN ones are
                         gone: LEAN is never wagered, so their "units"
                         were a hypothetical −110 sum wearing the same
                         type and colour as real money.
     * LeanBlock         was the sum of those two LEAN cards.  Replaced
                         by a sentence carrying the COUNT and no money.
     * the pass row      a window-wide count of games the model declined.
                         The board shows every one of them individually,
                         with the reason, and the board header counts
                         them.
     * ClvStat           closing-line value is not measurable on this
                         system (we lock the first price we see), so the
                         tile could only ever print a disclaimer.  The
                         disclaimer survives as a footnote; the freed
                         slot went to "Priced", which is the number that
                         actually says whether the headline is real.
     * SeasonRecordCard  the model replay moved inside the collapsed
                         "How this number was computed" box on the same
                         card as the headline it explains.  Nothing was
                         dropped — bank arrow, stakes, drawdown, date
                         range, no-hindsight check, method and caveat
                         are all in there.
     * MigrationNote     was never mounted anywhere.

   TWO RULES THIS FILE OBEYS:
     * EVERY COUNT COMES FROM lib/reconcile.  Never recomputed here.
     * A HUE MEANS REAL MONEY.  Peach up, rust down, amber = something
       needs a decision.  Simulated figures are never tone-coloured.
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
  /** The night's counts, resolved ONCE by DashboardShell and handed to
   *  the ticker, the hero and this panel, so the three chains cannot
   *  read different datasets.  Optional: when it is absent this panel
   *  resolves it exactly the same way (lib/reconcile, never local
   *  arithmetic). */
  night?: NightCounts | null;
}

export function RoiPanel({ initialDate, rows, details, seasonRecord, night: nightProp }: RoiPanelProps) {
  const [window, setWindow]   = useState<RoiWindow>("today");
  const [data, setData]       = useState<RoiResponse | null>(null);
  const [loading, setLoading] = useState(false);

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
  // the board, whose replay fields are null (never 0).  If the shell
  // already resolved it, use ITS object so the ticker, the hero and this
  // table cannot read different datasets.
  const night = nightProp ?? fromRec ?? tonight;

  // The superseded flat-1u ledger, STRONG only.  LEAN never reaches this
  // block: nothing was wagered on it, so it has no P&L to show.
  const ledgerZones = (view?.betZones ?? []).filter((z) => z.strength === "STRONG");
  const leanCalls   = view?.leanPaperTrade?.picks ?? 0;

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <div className={styles.headLeft}>
          <span className="eyebrow">Performance</span>
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
        {/* THE SYSTEM leads: the current model at quarter-Kelly. This is
            what the operator runs, and the only record that answers "how
            am I doing". Dates before 2026-07-28 are that model replayed;
            from 7/28 it is live. */}
        <SystemCard
          rec={seasonRecord}
          side={seasonRecord?.projected ?? seasonRecord?.real ?? null}
          bankUnits={seasonRecord?.startBank ?? 100}
          startDate={view?.startDate}
          endDate={view?.endDate}
          window={window}
        />

        {/* The old flat-1u ledger. Real money that really moved, so it is
            not deleted -- but it is the PREVIOUS gate at a stake the
            system no longer uses, so it gets one quiet block rather than
            a grid of its own. */}
        {ledgerZones.length > 0 && (
          <div className={styles.ledgerBlock}>
            <span className="eyebrow">Older ledger · flat 1u · superseded 2026-07-28</span>
            {ledgerZones.map((z) => (
              <LedgerRow key={z.label} zone={z} />
            ))}
          </div>
        )}
        {view && ledgerZones.length === 0 && (
          <div className={styles.emptyZone}>
            {window === "today"
              ? "No graded bets tonight yet."
              : "No graded bets in this window yet."}
          </div>
        )}

        {/* LEAN calls: a COUNT, deliberately with no money on it. They are
            never wagered, so any P&L here would be imaginary -- and an
            imaginary figure in the same shape as a real one is what made
            this panel hard to trust. The calls themselves are still on
            the board, game by game, with their prices. */}
        {leanCalls > 0 && (
          <p className={styles.leanLine}>
            <span className="num">{leanCalls}</span> LEAN {leanCalls === 1 ? "call" : "calls"} in
            this window — never bet, so no P&amp;L is shown. They appear game by
            game on the board.
          </p>
        )}

        {/* ── ZONE 3 · WHY ──────────────────────────────────────────
            A visible boundary, on the page background with no card. It
            tells the operator he has left the money and entered the
            reference material -- which is the visual form of "these
            counts are ALLOWED to differ". */}
        <div className={`zoneWhy ${styles.whyZone}`}>
          <div className="zoneHead">
            <span className="eyebrow">Why the system did that</span>
          </div>
          <DayReconcile
            day={recDay}
            tonight={night}
            selectedDate={initialDate}
            sideFrom={recSide?.from}
            sideTo={recSide?.to}
          />
        </div>
      </div>
    </section>
  );
}

/** THE SYSTEM -- current model, quarter-Kelly, for the selected window.
 *
 *  HEADLINE IS A PERCENTAGE, deliberately. The run compounds from 100u in
 *  April, so by late July the bank is ~316u and an ordinary losing week
 *  prints as "-58.85u" -- which against a real 100u bankroll is about
 *  -16u. A unit figure is meaningless without the bank it was staked
 *  from; the percentage is the same number wherever the run has got to.
 *  Units are still shown, next to the bank they belong to.
 *
 *  NRFI is reported SEPARATELY and never folded into the headline:
 *  _LR_STRONG_NRFI_P is 1.01, so the live system does not place it, and
 *  counting it would report losses on bets that were never made.
 *
 *  The card now carries three things that used to be separate surfaces:
 *  the CLV footnote (was a stat tile that could only say "Not
 *  measurable"), the reconciler sentence, and the whole model replay
 *  inside a collapsed disclosure. */
function SystemCard({
  rec, side, bankUnits, startDate, endDate, window: win,
}: {
  rec: RecFile | null;
  side: RecSide | null;
  /** The operator's ACTUAL bankroll (100u). Every unit figure on this
   *  card is rebased to it. The raw simulation compounds to ~1200u by
   *  late July, so its own units describe a bankroll nobody has. */
  bankUnits: number;
  startDate: string | undefined;
  endDate: string | undefined;
  window: RoiWindow;
}) {
  const w = replayWindow(side, startDate, endDate);
  const label = win === "season" ? "Season to date"
    : win === "today" ? "Tonight"
    : win === "7d" ? "Last 7 days" : "Last 30 days";

  const y = w?.yrfi ?? null;
  const tone: "win" | "loss" | "neutral" =
    !y ? "neutral" : y.pnl > 0.05 ? "win" : y.pnl < -0.05 ? "loss" : "neutral";
  const pct = w && y && isNum(w.bankStart) && w.bankStart > 0 ? y.pnl / w.bankStart : null;

  return (
    <div className={styles.moneyCard} data-tone={tone}>
      <div className={styles.totalLeft}>
        <span className="eyebrow">The system · ¼-Kelly · {label}</span>
        {w && y ? (
          <>
            <span className="figHero">
              {pct == null ? fmtU(y.pnl)
                : `${pct >= 0 ? "+" : "−"}${Math.abs(100 * pct).toFixed(1)}%`}
            </span>
            <span className="meta">
              {pct == null
                ? `${y.bets} ${y.bets === 1 ? "bet" : "bets"}`
                : `${fmtU(pct * bankUnits)} on your ${bankUnits.toFixed(0)}u bankroll`}
              {` · ${y.bets} ${y.bets === 1 ? "bet" : "bets"} · ${y.wins}-${y.bets - y.wins} · YRFI · ${w.from} → ${w.to}`}
            </span>
            {w.assumed > 0 && (
              <span className={styles.provNote}>
                {/* Amber: a price we never captured is a thing that needs
                    the operator's attention, which is what amber means
                    now. */}
                <span className={styles.provDot} data-attn="1" aria-hidden />
                {`${w.assumed} of ${w.bets} bets had no captured DraftKings price and `}
                {`were priced at an assumed −125. Assume −155 instead and the `}
                {`season's simulated bank falls by roughly a third.`}
              </span>
            )}
            {w.nrfi.bets > 0 && (
              <span className={styles.provNote}>
                <span className={styles.provDot} aria-hidden />
                {`NRFI is tracked but NOT bet — the live gate is switched off. `}
                {`${w.nrfi.bets} would-be ${w.nrfi.bets === 1 ? "bet" : "bets"}, `}
                {`${w.nrfi.wins}-${w.nrfi.bets - w.nrfi.wins}`}
                {isNum(w.bankStart) && w.bankStart > 0
                  ? `, ${fmtU((w.nrfi.pnl / w.bankStart) * bankUnits)}` : ""}
                {`. Not counted above.`}
              </span>
            )}
          </>
        ) : (
          <>
            <span className="figHero">—</span>
            <span className="meta">No bets settled in this window yet.</span>
          </>
        )}
      </div>

      <div className={styles.totalRight}>
        <Stat label="Record"
          value={w && y ? `${y.wins}-${y.bets - y.wins}` : "0-0"} />
        <Stat label="Hit rate"
          value={y && y.bets > 0 ? `${(100 * y.wins / y.bets).toFixed(1)}%` : "—"} />
        {/* Replaces the closing-line-value tile. This is the number that
            governs whether the headline above is real money or a priced
            guess, and it was nowhere on the panel before. */}
        <Stat
          label="Priced"
          value={w ? `${w.bets - w.assumed} of ${w.bets}` : "—"}
          caption="at a captured DraftKings price"
          muted
        />
      </div>

      {/* The closing-line-value explanation, kept word for word. The tile
          it used to live in only ever printed "Not measurable", which is
          a disclaimer, not a statistic. */}
      <p className={styles.clvFootnote}>
        Closing-line value is not measurable here — we bet the first price
        we see and lock it, so there is no closing price to compare
        against. Per-game moves, when they happen, still show in the odds
        chip on the board.
      </p>

      {/* Nothing on this page said this before, and it is the whole answer
          to "why does it say 43-38 up there and 112 bets 58-54 down
          here". */}
      <p className={styles.reconciler}>
        <b>Above:</b> YRFI bets inside the window you picked. <b>Inside
        &ldquo;How this number was computed&rdquo;:</b> the same replay over
        its whole run, both sides. Different populations, so the bet counts
        differ.
      </p>

      {rec && <HowComputed rec={rec} />}
    </div>
  );
}

/** THE MODEL REPLAY, folded into the card whose headline it explains.
 *
 *  This is the whole of the old "Model replay" card. It is collapsed
 *  because every figure in it is SIMULATED, and a back-test must not sit
 *  as a peer of the real ledger -- but nothing was dropped, and the
 *  compounded bank arrow the operator has been reading as his season is
 *  still here, now with a sentence saying what it actually is.
 *
 *  The side shown is `projected`, which is the SAME object SystemCard
 *  leads with (RoiPanel passes projected ?? real). That is deliberate:
 *  the two can now differ only by date window, never by population. */
function HowComputed({ rec }: { rec: RecFile }) {
  const real = rec.real;
  return (
    <details className={styles.howBox}>
      <summary>How this number was computed</summary>

      <p>
        <span className="tag">Simulated</span> {rec.headlineMethod}
      </p>

      {rec.projected ? (
        <ReplayBody side={rec.projected} />
      ) : (
        <p>
          Model replay unavailable — the export ran but produced no staked
          bets for this side.
        </p>
      )}

      {/* The one fact the deleted "Real prices" column carried that the
          projected column does not: the same replay restricted to bets
          that had a genuine captured DraftKings price. */}
      {real && isNum(real.sim.profit) && (
        <p>
          {`Over real captured prices only (${real.from} → ${real.to}): `}
          {`${fmtU(real.sim.profit)}, ${real.bets} ${real.bets === 1 ? "bet" : "bets"}, `}
          {`${real.wins}-${real.losses}.`}
        </p>
      )}

      <p>The flat-1u ledger below was superseded on 2026-07-28.</p>
      <p>{rec.caveat}</p>
    </details>
  );
}

/** The replay's own figures. Every one of these was on the deleted
 *  "Model replay" card and every one of them is still here. */
function ReplayBody({ side }: { side: RecSide }) {
  const k = side.sim;
  return (
    <>
      <p>
        {`Whole season · ¼-Kelly compounded: ${isNum(k.profit) ? fmtU(k.profit) : "—"} over `}
        {`${side.bets} ${side.bets === 1 ? "bet" : "bets"}, ${side.wins}-${side.losses}`}
        {isNum(side.hitRate) ? `, ${(100 * side.hitRate).toFixed(1)}% hit` : ""}.
      </p>

      {/* THE BIG BANKROLL NUMBER. Demoted to one sentence, never
          removed -- this is the figure the operator has historically
          read as his season. */}
      {isNum(k.finalBank) && isNum(k.startBank) && (
        <p>
          Simulated bank{" "}
          <span className={styles.simBank}>
            {k.startBank.toFixed(0)}u → {k.finalBank.toFixed(2)}u
          </span>{" "}
          — a compounding back-test on an imaginary bank, not your ledger.
        </p>
      )}

      {/* What compounding actually asks of you. An average hides it: on
          the projected path the bank grows ~10x, so the 10%-per-bet cap
          puts late stakes near 80u. */}
      {isNum(k.medianStake) && (
        <p>
          {`Typical bet ${k.medianStake.toFixed(2)}u · biggest ${(k.largestStake ?? 0).toFixed(2)}u`}
          {isNum(k.maxDrawdownPct) ? ` · deepest drawdown ${k.maxDrawdownPct.toFixed(1)}%` : ""}.
        </p>
      )}

      <p>
        {side.from} → {side.to}
        {side.priceFill != null
          ? ` · missing prices filled at ${side.priceFill} (${side.assumedBets} of ${side.bets})`
          : " · real captured prices only"}
        {isNum(side.selectedBets) && (side.droppedZeroStake ?? 0) > 0 && (
          <> · staked {side.bets} of {side.selectedBets} qualifying ({side.droppedZeroStake} sized to zero)</>
        )}
      </p>

      {side.floor && (
        <p>
          {`No-hindsight check: ${fmtU(side.floor.sim.profit)} over ${side.floor.bets} bets `}
          {`(${side.floor.wins}-${side.floor.losses}), same ¼-Kelly staking.`}
        </p>
      )}
    </>
  );
}

/** One row of the superseded flat-1u ledger.
 *
 *  Was a card in a four-up grid. Every figure it printed is still here:
 *  the label, the W-L, the units, the hit rate, the points versus
 *  break-even, the bar and its 52.38% break-even tick. What it lost is
 *  the card shape, which is what made a never-bet LEAN tier look like a
 *  peer of real money. */
function LedgerRow({ zone }: { zone: ZoneRoi }) {
  const tone = zoneTone(zone);
  return (
    <div className={styles.ledgerRow} data-side={zone.side} data-tone={tone}>
      <span className={styles.ledgerLabel}>{zone.label}</span>
      <span className={styles.ledgerCount}>
        {zone.bets > 0 ? `${zone.wins}-${zone.losses}` : "—"}
      </span>
      <div className={styles.ledgerMid}>
        <div className={styles.ledgerBar}>
          <span
            className={styles.ledgerBarFill}
            style={{ width: `${barWidthPct(zone.hitRate)}%` }}
            aria-hidden
          />
          <span className={styles.ledgerBarBE} aria-hidden />
        </div>
        <span className={styles.ledgerSub}>
          {zone.bets > 0 ? (
            <>
              <span><span className="num">{pctText(zone.hitRate)}</span> hit</span>
              <span className={styles.ledgerSep} aria-hidden>·</span>
              <span>
                <span className="num">{signedPctText(zone.edgeVsBreakEven)}</span> vs break-even
              </span>
            </>
          ) : (
            <span>{zone.picks} picks, none graded yet</span>
          )}
        </span>
      </div>
      <span className={styles.ledgerUnits}>
        {zone.bets > 0 ? fmtU(realPL(zone)) : "—"}
      </span>
    </div>
  );
}

function Stat({ label, value, caption, muted }: {
  label: string; value: string; caption?: string; muted?: boolean;
}) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={`figStat ${muted ? styles.statProv : ""}`}>{value}</span>
      {caption && <span className={styles.statCaption}>{caption}</span>}
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

/** Tone follows realPL -- the number the row PRINTS. It used to key off
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

function barWidthPct(hitRate: number): number {
  if (Number.isNaN(hitRate)) return 0;
  return Math.max(0, Math.min(100, hitRate * 100));
}

export type { ZoneProvenance };
