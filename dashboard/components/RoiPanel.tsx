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
  const ledgerZones = (view?.betZones ?? []).filter((z) => z.strength === "STRONG");

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
          side={seasonRecord?.projected ?? seasonRecord?.real ?? null}
          bankUnits={seasonRecord?.startBank ?? 100}
          startDate={view?.startDate}
          endDate={view?.endDate}
          window={window}
          tonight={tonightLive}
        />

        {/* The old flat-1u ledger. Real money that really moved, so it is
            not deleted -- but it is the PREVIOUS gate at a stake the
            system no longer uses, so it gets one quiet block rather than
            a grid of its own. */}
        {/* 2026-07-29 distill pass -- COLLAPSED BY DEFAULT.
            This block reports the SAME nights as the system card above
            it, under an accounting method the system stopped using on
            2026-07-28, and its own footnote concedes the figures rest on
            a placeholder −110 rather than a price that was ever paid.
            Open on the primary surface, it put a knowingly-wrong number
            at eye level immediately below the right one, with an apology
            attached -- the operator read both and trusted neither.

            NOTHING IS DELETED. It is real money that really moved, the
            rows are byte-identical, and one tap still reaches them. */}
        {ledgerZones.length > 0 && (
          <details className={styles.ledgerBlock}>
            <summary className={styles.ledgerSummary}>
              <span className="eyebrow">
                Older ledger · flat 1u · superseded 2026-07-28
              </span>
            </summary>
            {ledgerZones.map((z) => (
              <LedgerRow key={z.label} zone={z} />
            ))}
          </details>
        )}
        {view && ledgerZones.length === 0 && (
          <div className={styles.emptyZone}>
            {window === "today"
              ? "No graded bets tonight yet."
              : "No graded bets in this window yet."}
          </div>
        )}

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
  const pct = w && y && isNum(w.bankStart) && w.bankStart > 0 ? y.pnl / w.bankStart : null;

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
              {pct == null ? fmtU(y.pnl) : fmtU(pct * bankUnits)}
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
 *  FIX 6 (2026-07-28) — ONE POPULATION PER ROW.
 *
 *  This row used to print the record and the hit rate over ALL graded
 *  bets, next to a "vs break-even" figure and a units figure computed
 *  over the REAL-PRICED subset only.  STRONG NRFI rendered:
 *
 *      STRONG NRFI · 57-39 · 59.4% hit · −13.0pp vs break-even · −11.29u
 *
 *  which cannot be true of any single set of bets: 59.4% is 13 points
 *  under break-even only if break-even is 72.4%.  The record and the hit
 *  rate were 96 graded bets; the pp and the −11.29u were the 49 of those
 *  that had a captured DraftKings price, and those 49 went 22-27 (44.9%)
 *  against a true break-even of 57.87%.  The bar made it visible: the
 *  fill ran to 59.4% and visibly cleared a tick hard-pinned at 52.38% on
 *  a row whose units said the zone lost money.
 *
 *  Now every figure on the row — record, hit rate, bar fill, break-even
 *  tick, points-vs-break-even and units — describes the same bets, and a
 *  line underneath says how many bets that is out of how many graded. */
function LedgerRow({ zone }: { zone: ZoneRoi }) {
  const tone = zoneTone(zone);
  const pop  = ledgerPopulation(zone);
  return (
    <div className={styles.ledgerRow} data-side={zone.side} data-tone={tone}>
      <span className={styles.ledgerLabel}>{zone.label}</span>
      <span className={styles.ledgerCount}>
        {pop.graded > 0 ? `${pop.wins}-${pop.losses}` : "—"}
      </span>
      <div className={styles.ledgerMid}>
        <div className={styles.ledgerBar}>
          <span
            className={styles.ledgerBarFill}
            style={{ width: `${barWidthPct(pop.hitRate)}%` }}
            aria-hidden
          />
          {/* The tick is the price this zone ACTUALLY paid, not a
              hardcoded −110.  Inline because it is a datum, not a
              style: 55.8% on YRFI, 57.9% on NRFI. */}
          <span
            className={styles.ledgerBarBE}
            style={{ left: `calc(${(pop.breakEven * 100).toFixed(2)}% - 1px)` }}
            aria-hidden
          />
        </div>
        <span className={styles.ledgerSub}>
          {pop.graded > 0 ? (
            <>
              <span><span className="num">{pctText(pop.hitRate)}</span> hit</span>
              <span className={styles.ledgerSep} aria-hidden>·</span>
              <span>
                <span className="num">{signedPctText(pop.hitRate - pop.breakEven)}</span>
                {" vs break-even "}
                <span className="num">{pctText(pop.breakEven)}</span>
              </span>
            </>
          ) : (
            <span>{zone.picks} picks, none graded yet</span>
          )}
        </span>
      </div>
      <span className={styles.ledgerUnits}>
        {pop.graded > 0 ? fmtU(realPL(zone)) : "—"}
      </span>

      {/* WHICH BETS THE ROW ABOVE IS ABOUT.  Without this the operator
          reads a smaller record than he remembers placing and assumes
          rows went missing — his single most common report. */}
      {pop.graded > 0 && (
        <span className={styles.ledgerProv}>
          {pop.real ? (
            zone.bets > pop.graded ? (
              <>
                {`${pop.graded} of ${zone.bets} graded ${zone.bets === 1 ? "bet" : "bets"} had a `}
                {`captured DraftKings price. The other ${zone.bets - pop.graded} settled against a `}
                {`placeholder −110 and are not counted here — nobody knows what those actually `}
                {`paid, so including them would invent a result.`}
              </>
            ) : (
              <>
                {`All ${pop.graded} graded ${pop.graded === 1 ? "bet" : "bets"} had a captured `}
                {`DraftKings price, so every figure above is a price you really paid.`}
              </>
            )
          ) : (
            <>
              {`No bet in this zone has a captured DraftKings price, so every figure above `}
              {`rests on a placeholder −110 (break-even 52.4%) rather than a price you paid.`}
            </>
          )}
        </span>
      )}
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
