"use client";

import { useEffect, useMemo, useState } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import type { RoiResponse, RoiWindow, ZoneRoi, ZoneProvenance } from "@/lib/roi";
import { aggregateTodayRoi } from "@/lib/roi-today";
import type { RecFile, RecSide } from "@/lib/season-record";
import { isNum, replayWindow } from "@/lib/season-record";
import type { NightCounts } from "@/lib/reconcile";
import type { TonightSystem } from "@/lib/reconcile";
import { nightFromRecord, nightFromBoard, tonightFromBoard, fmtU } from "@/lib/reconcile";
/* 2026-07-30 unit re-basing. `fmtU` stays for the PER-NIGHT and
   PER-BET figures on this panel, which are real quantities. The
   season-long ones move to bank growth / a return -- see lib/units. */
import {
  formatBankGrowth, formatReturn, returnAsUnits, bankReturn, EM_DASH,
} from "@/lib/units";
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

/** Resolve a date against the record: REAL first (real captured prices),
 *  then PROJECTED, because REAL only starts 2026-05-07 and the 36 April
 *  dates exist only in the projected replay. Exported so DashboardShell
 *  can mount DayReconcile below the board with the same resolution. */
export function resolveRecordDay(rec: RecFile | null, date: string) {
  const realSide = rec?.real ?? null;
  const projSide = rec?.projected ?? null;
  const realDay  = realSide?.days.find((d) => d.date === date) ?? null;
  const projDay  = projSide?.days.find((d) => d.date === date) ?? null;
  return {
    day:  realDay ?? projDay,
    side: realDay ? realSide : projDay ? projSide : (realSide ?? projSide),
  };
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
  const recDay   = resolveRecordDay(seasonRecord, initialDate).day;
  // Tonight is not in the nightly export yet, so read it off the board.
  const tonightLive = useMemo(
    () => tonightFromBoard(rows, details),
    [rows, details],
  );
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

  // FIX 2 (2026-07-28) — THE HEADER SAID THE SEASON STARTED IN JANUARY.
  //
  // The season window's startDate is built as `<year>-01-01`, so the range
  // printed "2026-01-01 → 2026-07-28" — 209 days — next to a
  // "across 116 days" count, implying 93 blank days the operator kept
  // asking about.  The first row in the ledger is 2026-04-01.
  //
  // This is a DISPLAY clamp only: the first date the cumulative series
  // actually carries is, by construction, the earliest date in the window
  // with a graded bet.  It is applied to `season` alone — on 7d/30d the
  // requested start is a real boundary the operator chose and must keep
  // seeing, even if the first day or two graded nothing.
  const displayStart = useMemo(() => {
    if (!view) return "";
    if (window !== "season") return view.startDate;
    const firstGraded = view.cumulativePL?.[0]?.date;
    return firstGraded && firstGraded > view.startDate ? firstGraded : view.startDate;
  }, [view, window]);

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <div className={styles.headLeft}>
          <span className="eyebrow">Performance</span>
          {view && (
            <span className={styles.range}>
              {window === "today"
                ? <>Tonight&rsquo;s slate</>
                : <>{displayStart} → {view.endDate}</>}
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
          /* REAL PRICES FIRST (2026-07-29).  Was `projected ?? real`.
           *
           * `projected` fills every bet with no captured DraftKings
           * price at an assumed -125 -- 62 of its 194 bets, a third of
           * the book. Compounded through quarter Kelly that assumption
           * is worth an enormous amount: projected simulates 100u ->
           * 866u (+766u) where the same strategy on REAL prices only
           * simulates 100u -> 227u (+127u). The operator read the
           * bigger figure as their season and asked "i thought we were
           * up 90+ units" -- the honest answer needed three numbers to
           * untangle, and this was the loudest wrong one.
           *
           * `real` is the same model, same gate, same quarter-Kelly
           * sizing, on the subset of bets whose price was actually
           * observed. It starts 2026-05-07 because nothing before that
           * has captured prices. Fewer bets, smaller number, no
           * invented money.
           *
           * `projected` is NOT deleted -- it is still in the record
           * file and still rendered inside "How this number was
           * computed", where the price assumption is stated next to it. */
          side={seasonRecord?.real ?? seasonRecord?.projected ?? null}
          bankUnits={seasonRecord?.startBank ?? 100}
          startDate={view?.startDate}
          endDate={view?.endDate}
          window={window}
          tonight={tonightLive}
        />

        {/* THE "OLDER LEDGER · FLAT 1U · SUPERSEDED" BLOCK WAS REMOVED
            HERE 2026-07-30, at the operator's request: "remove the old
            ledger entirely ... all i care about is the new system, the
            new kelly sizing, and the new total profit."

            It reported the same nights as the system card above under an
            accounting method retired on 2026-07-28 (flat 1u, the looser
            gate, NRFI still live), and its own footnote conceded the
            figures rested on a placeholder −110 rather than a price
            anyone paid. Season-wide that ledger is −8.93u, of which
            −11.29u is NRFI bets from a strategy switched off on
            2026-06-07. It was answering a question the operator has
            stopped asking, in the loudest position on the panel.

            NO DATA WAS DELETED, and that is deliberate — the operator
            wants to be able to ask "what would flat 1u have done"
            later. Every row is untouched in data/picks_2026.csv and
            Supabase; `tools/pl_calc.py` reports it, and
            `tools/kelly_season_backfill.py` compares flat against every
            Kelly fraction on demand. This removes a RENDER, not a
            record. Recover the block from git history if the flat-1u
            view is ever wanted back on screen. */}

        {/* CUT 2026-07-28 — the LEAN calls line.
            It read: "N LEAN calls in this window — never bet, so no P&L is
            shown. They appear game by game on the board."  A sentence
            whose entire content is that it has no content.  Nothing is
            pending a decision on LEAN either: season LEAN YRFI is 14
            graded at 35.7% and LEAN NRFI 263 at 46.0%, both well under
            break-even, and the playbook's tier-expansion criterion is 60
            graded.  If LEAN ever becomes a live question it earns an
            evaluation, not a count.  The calls themselves are unchanged
            and still on the board, game by game, with their prices. */}

        {/* ZONE 3 ("why the system did that") used to render here. The
            operator asked for the record at the TOP of the page, so this
            panel moved above the board -- and a long reconciliation table
            above the board would bury the slate. DayReconcile is now
            mounted by DashboardShell BELOW the board, where reference
            material belongs. Nothing was removed. */}
      </div>
    </section>
  );
}

/** THE SYSTEM -- current model, quarter-Kelly, for the selected window.
 *
 *  HEADLINE IS UNITS (changed 2026-07-29; it was a percentage).
 *
 *  The old rationale, kept because it explains what NOT to undo: the raw
 *  replay compounds from 100u in April to ~1200u by late July, so ITS
 *  units describe a bankroll nobody has, and quoting them raw would
 *  print "-58.85u" for a week that cost the operator about 16u.
 *
 *  The fix for that is the REBASING, not the percentage -- and the
 *  rebasing is already here, via `bankUnits`. With both in place the
 *  headline read "+16.3%" over a sub-line reading "+16.33u on your 100u
 *  bankroll": the same number twice, since at a 100u bank a unit and a
 *  percent are numerically identical. The percentage was costing the
 *  operator a translation step to reach the only unit they actually
 *  stake in.
 *
 *  KEEP the rebasing. If the operator ever moves off a 100u bank the two
 *  figures separate again, which is why the percentage stays on the
 *  sub-line rather than being deleted.
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
  rec, side, bankUnits, startDate, endDate, window: win, tonight,
}: {
  rec: RecFile | null;
  side: RecSide | null;
  /** Tonight straight off the board. The nightly export only covers fully
   *  graded days, so on the live slate the record card had nothing to show
   *  even after tonight's pick graded. */
  tonight: TonightSystem | null;
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

  // TONIGHT comes from the board, not the record. Everything else comes
  // from the replay.
  if (win === "today") {
    const t = tonight;
    const tone: "win" | "loss" | "neutral" =
      !t || (t.wins + t.losses) === 0 ? "neutral"
      : t.pnl > 0.05 ? "win" : t.pnl < -0.05 ? "loss" : "neutral";
    const settled = t ? t.wins + t.losses : 0;
    return (
      <div className={styles.moneyCard} data-tone={tone}>
        <div className={styles.totalLeft}>
          <span className="eyebrow">The system · ¼-Kelly · Tonight</span>
          <span className="figHero">
            {!t ? "—" : settled === 0 ? `${t.staked.toFixed(2)}u` : fmtU(t.pnl)}
          </span>
          <span className="meta">
            {!t
              ? "No STRONG picks on tonight's slate."
              : settled === 0
                ? `sized across ${t.bets} STRONG ${t.bets === 1 ? "pick" : "picks"} · none graded yet`
                : `${t.wins}-${t.losses} settled · ${t.staked.toFixed(2)}u sized across ` +
                  `${t.bets} STRONG ${t.bets === 1 ? "pick" : "picks"}` +
                  (t.pending > 0 ? ` · ${t.pending} still running` : "")}
          </span>
          {t && t.committed < t.bets && (
            <span className={styles.provNote}>
              <span className={styles.provDot} data-attn="1" aria-hidden />
              {`${t.bets - t.committed} of ${t.bets} sized ${t.bets - t.committed === 1 ? "pick was" : "picks were"} `}
              {`never committed to the ledger — a STRONG pick stays pending until its `}
              {`lock window opens, and these graded before that happened. The figure `}
              {`above is what the system sized; the ledger below booked nothing for them.`}
            </span>
          )}
        </div>
        <div className={styles.totalRight}>
          <Stat label="Record" value={t ? `${t.wins}-${t.losses}` : "0-0"} />
          <Stat label="Hit rate"
            value={settled > 0 ? `${(100 * (t?.wins ?? 0) / settled).toFixed(1)}%` : "—"} />
          <Stat label="Committed" value={t ? `${t.committed}/${t.bets}` : "—"} />
        </div>
      </div>
    );
  }

  const y = w?.yrfi ?? null;
  // NEUTRAL, ALWAYS -- this branch is the REPLAY (see the comment above
  // the `today` early-return: "Everything else comes from the replay").
  //
  // 2026-07-29.  This used to compute win/loss from y.pnl and tone the
  // headline accordingly, which put a 32px money-coloured "+822.19u" on
  // the SEASON tab.  Quarter-Kelly went live 2026-07-28; every bet
  // before that was flat 1u.  So that figure is a backtest of a staking
  // scheme the operator was not using, rendered identically to TONIGHT's
  // real −2.08u -- same size, same position, same hue.
  //
  // globals.css, verbatim: "SIMULATED FIGURES ARE NEVER TONE-COLOURED.
  // Coloured = your money.  Neutral = a back-test.  No exception, no
  // carve-out."  This was the exception.  PRODUCT.md names it as the
  // product's central design problem -- four kinds of number at
  // near-identical visual weight -- and as an anti-reference: "anything
  // that makes a simulated or paper number look like realized P&L."
  //
  // The real money for these windows is the flat-1u ledger block below.
  const tone = "neutral" as const;
  /* FROM THE BANK'S TWO ENDPOINTS (2026-07-30), not from summing the
     per-game P&L.

     `y.pnl / w.bankStart` is very nearly right -- the bank IS bankStart
     plus the YRFI P&L, so the ratio is exact in principle. In practice
     the exporter rounds per-game `pnl` and the bank series separately,
     and the two drift: this card read +109.95u for the season while
     /history read +109.89u for the same bets on the same day. Six
     hundredths of a unit is nothing as money and everything as trust --
     it is precisely the "two legitimate figures side by side measuring
     the same thing differently" defect this dashboard has been cleared
     of repeatedly.

     Both pages now divide the same two bank levels, so they cannot
     disagree by construction rather than by luck. */
  const pct = w ? bankReturn(w.bankStart ?? NaN, w.bankEnd ?? NaN) : null;

  return (
    <div className={styles.moneyCard} data-tone={tone}>
      <div className={styles.totalLeft}>
        <span className="eyebrow">
          The system · ¼-Kelly · {label} <span className="tag">Simulated</span>
        </span>
        {w && y ? (
          <>
            {/* UNITS LEAD, percentage follows (2026-07-29).
                This was the other way round, and the docstring above
                explains why: a raw simulation that compounds to ~316u
                makes a unit figure meaningless without naming its bank.
                That reasoning is now obsolete -- the card ALREADY
                rebases every figure to the operator's real bank via
                `bankUnits`. Which made the headline "+16.3%" and the
                sub-line "+16.33u on your 100u bankroll": the SAME
                NUMBER twice, because the bank is 100u so a unit IS a
                percent. The operator stakes in units, is told to bet
                5.97u, and was then shown their result in the one unit
                they don't think in. Operator: "why are the last X days
                filters showing percentages and not units? im so
                confused". */}
            <span className="figHero">
              {/* 2026-07-30: the pct==null fallback used to print
                  `y.pnl` -- units added across every day in the window,
                  which is not a money quantity once the bank has moved.
                  There is no honest unit figure when the opening bank
                  is unknown, so it now shows an em dash and the bet
                  count carries the sentence. The main branch is
                  unchanged and was already correct: a return times a
                  100-unit bank IS a unit count, because a unit is 1% of
                  bank by definition. */}
              {pct == null ? EM_DASH : returnAsUnits(pct)}
            </span>
            <span className="meta">
              {pct == null
                ? `${y.bets} ${y.bets === 1 ? "bet" : "bets"}`
                : `${pct >= 0 ? "+" : "−"}${Math.abs(100 * pct).toFixed(1)}% on your ${bankUnits.toFixed(0)}u bankroll`}
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
            {/* WHY THE WINDOW IS SHORTER THAN THE ONE YOU PICKED.
                The header above says the season starts 2026-04-01, but
                this card reports from 2026-05-07, and nothing said why.
                `real` only covers dates where DraftKings prices were
                actually captured -- April has none, so it is not that
                the model sat out, it is that those nights cannot be
                scored against a price anyone paid. Stated rather than
                left as an unexplained date mismatch. */}
            {startDate && w.from > startDate && (
              <span className={styles.provNote}>
                <span className={styles.provDot} aria-hidden />
                {`Starts ${w.from}, not ${startDate}: no DraftKings prices were `}
                {`captured before then, so those nights cannot be scored at a `}
                {`price you would have paid.`}
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
        {/* THE "FLAT 1U" TILE WAS REMOVED 2026-07-30.
            Operator: "i wanted to remove the flat unit tracking in the
            dashboard". The system stakes quarter-Kelly; a flat figure
            describes a staking scheme nobody runs. It also spent a day
            silently measuring a DIFFERENT set of bets than the headline
            beside it (it summed day-level totals, which include NRFI,
            against a YRFI-only headline). The slot before it held
            "Priced: N of N", which went degenerate when the headline
            moved to real prices only.
            Two stats is the honest count here: the record and the hit
            rate. `flatProfit` is still in season_record.json per side
            and per month if the question ever comes back. */}
      </div>

      {/* 2026-07-28: the CLV disclaimer and the two-populations
          reconciler paragraph were removed on operator request to simplify
          this section. Neither was a statistic -- one printed "not
          measurable" as prose, the other explained why two bet counts
          differ. The counts they reconciled are now only shown in one
          place (the collapsed "how this number was computed"), so the
          explanation is no longer load-bearing. */}

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
 *  THE BOX SHOWS `projected` ON PURPOSE, and since 2026-07-29 that is
 *  NOT what the headline above leads with (that is now `real`). This is
 *  the one place the price-assumption figure belongs: inside the
 *  disclosure that exists to explain the assumption, stated next to it,
 *  rather than as a 32px headline the operator reads as their season.
 *  ReplayBody prints the fill and the assumed-bet count. */
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
          {/* BANK GROWTH, not a summed profit (2026-07-30). This
              printed `sim.profit`, which is finalBank - startBank --
              arithmetically a level difference, but presented as a unit
              total it invites exactly the addition the unit model
              forbids. The two endpoints say the same thing and cannot
              be misread as something to add to another window's. */}
          {`Over real captured prices only (${real.from} → ${real.to}): bank `}
          {`${formatBankGrowth(real.sim.startBank, real.sim.finalBank)}`}
          {isNum(bankReturn(real.sim.startBank, real.sim.finalBank))
            ? ` (${formatReturn(bankReturn(real.sim.startBank, real.sim.finalBank) as number)})`
            : ""}
          {`, ${real.bets} ${real.bets === 1 ? "bet" : "bets"}, `}
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
  const seasonRet = bankReturn(k.startBank, k.finalBank);
  const floorRet = side.floor
    ? bankReturn(k.startBank, side.floor.sim.finalBank)
    : null;
  return (
    <>
      <p>
        {/* A RETURN, not a unit total (2026-07-30). See the note on the
            real-prices sentence above; same reasoning, same fix. */}
        {`Whole season · ¼-Kelly compounded: `}
        {isNum(seasonRet) ? formatReturn(seasonRet) : "—"}
        {` over ${side.bets} ${side.bets === 1 ? "bet" : "bets"}, ${side.wins}-${side.losses}`}
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
          {/* Same re-basing as the headline: this compounds from the
              same 100u open, so its RETURN is directly comparable to
              the season figure above while a unit total is not. */}
          {`No-hindsight check: `}
          {isNum(floorRet) ? formatReturn(floorRet) : "—"}
          {` over ${side.floor.bets} bets `}
          {`(${side.floor.wins}-${side.floor.losses}), same ¼-Kelly staking.`}
        </p>
      )}
    </>
  );
}

// LedgerRow + its helpers were DELETED 2026-07-30 with the flat-1u
// ledger block they rendered. See the removal note in the panel body.
// The DATA is untouched; this was the only renderer.

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

/** THE ONE POPULATION a ledger row is allowed to describe.
 *
 *  Real-priced bets when the zone has any: their W-L, their hit rate, and
 *  the mean implied probability of the prices actually paid, which is the
 *  only honest break-even for them.  When a zone has none, everything it
 *  could print rests on the fabricated −110, so we say that in words on
 *  the row rather than dressing it up as a measurement.
 *
 *  0.5238 is the −110 break-even (110/210). It is used ONLY on the
 *  no-real-price fallback, where the row states outright that that is
 *  what it is doing. */
const PLACEHOLDER_BREAK_EVEN = 0.5238;

interface LedgerPopulation {
  /** true when these figures come from bets with a captured DK price */
  real:      boolean;
  wins:      number;
  losses:    number;
  graded:    number;
  hitRate:   number;
  breakEven: number;
}

function ledgerPopulation(z: ZoneRoi): LedgerPopulation {
  const p = z.provenance;
  const realGraded = p.realPricedWins + p.realPricedLosses;
  if (realGraded > 0) {
    return {
      real:      true,
      wins:      p.realPricedWins,
      losses:    p.realPricedLosses,
      graded:    realGraded,
      hitRate:   p.realPricedWins / realGraded,
      // realBreakEven is NaN until at least one real price is captured;
      // realGraded > 0 normally implies it is finite, but guard anyway --
      // a NaN here would put the tick at `calc(NaN% - 1px)` and the whole
      // declaration would be dropped, silently parking the tick at 0%.
      breakEven: Number.isFinite(p.realBreakEven) ? p.realBreakEven : PLACEHOLDER_BREAK_EVEN,
    };
  }
  return {
    real:      false,
    wins:      z.wins,
    losses:    z.losses,
    graded:    z.bets,
    hitRate:   z.hitRate,
    breakEven: PLACEHOLDER_BREAK_EVEN,
  };
}

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
