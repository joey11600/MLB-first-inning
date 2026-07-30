import { asCumulative, type CumulativeUnits } from "./units";

/**
 * Types for data/season_record.json, written by tools/export_season_record.py.
 *
 * WHY THIS FILE EXISTS (2026-07-28)
 * ---------------------------------
 * These interfaces used to live inline in RoiPanel.tsx and described a
 * FLAT shape (side.finalBank, side.kellyProfit, day.bets, day.bankAfter).
 * The exporter now nests the simulated bankroll under `sim`, adds the
 * no-hindsight `floor`, and replaces day.bets with day.games -- a richer
 * per-game reconciliation. A stale copy of these types is not a type
 * error, it is a RUNTIME CRASH: `side.kellyProfit.toFixed(2)` on a field
 * that no longer exists throws, the panel mounts and then dies, and the
 * operator sees a blank space where his money was.
 *
 * One definition, imported everywhere. If the exporter grows a field,
 * it is added here and nowhere else.
 *
 * NOTE: `projected` and `real` are NULLABLE. make_record() returns None
 * when a side stakes nothing, which serialises as JSON null.
 */

/** One bet or one skip, as the CURRENT MODEL would have handled it. */
export interface RecDisposition {
  action:  "BET" | "SKIP";
  /** BET only. */
  stake?:   number;
  odds?:    number;
  win?:     boolean;
  pnl?:     number;
  /** BET only: price was the -125 stand-in, not a captured DK number. */
  assumed?: boolean;
  /** SKIP only. Stable machine key -- switch on this, never on `reason`. */
  code?:    "gate" | "lambda_floor" | "lambda_ceiling" | "no_price"
          | "unscored" | "kelly_no_edge" | "daily_cap" | string;
  /** SKIP only. Prose for display; wording may change, `code` will not. */
  reason?:  string;
}

/** What the LIVE system did, for games it flagged STRONG. Absent when
 *  the ledger did not flag the game at all. */
export interface RecLedger {
  strength:     string;
  placed:       boolean;
  unitsRisked:  number | null;
  odds:         number | null;
  pnl:          number | null;
}

export interface RecGame {
  game:    string;          // "TOR@WSH", or "LAD@NYY G2" for a doubleheader leg
  side:    "NRFI" | "YRFI" | string;
  modelP:  number | null;   // p(no run in the 1st)
  record:  RecDisposition;
  ledger?: RecLedger;
}

export interface RecDay {
  date:          string;
  /** The REPLAY's flat-stake P&L. NOT the ledger's -- on 2026-07-27 this
   *  is -1.00 while the ledger sums to -0.33. Never label it "you". */
  flatPnl:       number;
  /** The REPLAY's Kelly-staked P&L. Simulated money. */
  simPnl:        number;
  simBankAfter:  number | null;
  flagged:       number;    // games the live ledger called STRONG
  placed:        number;    // of those, ones actually bet
  bet:           number;    // games the current model would bet
  games:         RecGame[];
}

export interface RecFloor {
  bets: number; wins: number; losses: number;
  hitRate: number; breakEvenNeeded: number; edgePts: number;
  flatProfit: number; assumedBets: number;
  sim: { finalBank: number; profit: number; maxDrawdownPct: number;
         largestStake?: number };
}

export interface RecSide {
  label:      string;
  priceFill:  number | null;   // -125 for projected, null for real
  from:       string;
  to:         string;
  bets:       number;
  wins:       number;
  losses:     number;
  hitRate:    number;
  breakEvenNeeded: number;
  edgePts:    number;
  /** Flat 1u per bet: the raw edge with no leverage. Kept as the
   *  reference line under the Kelly headline -- the operator stakes by
   *  Kelly now, so Kelly is what the card leads with. */
  flatProfit: number;
  assumedBets: number;
  selectedBets?:    number;
  droppedZeroStake?: number;
  droppedFlatPnl?:   number;
  /** A SIMULATION: quarter-Kelly compounding an imaginary 100u bank.
   *  Never the headline; always labelled. */
  sim: {
    startBank: number; finalBank: number; profit: number;
    maxDrawdownPct: number; kellyFraction: number;
    /** What compounding actually asks of the operator. With the bank up
     *  ~10x the 10%-per-bet cap makes late stakes large in absolute
     *  units, and an average hides that. */
    largestStake?: number;
    medianStake?: number;
  };
  /** The no-hindsight lower bound. null when it stakes nothing. */
  floor:   RecFloor | null;
  monthly: { month: string; bets: number; wins: number; losses: number;
             flat: number; assumedBets: number }[];
  days:    RecDay[];
}

export interface RecFile {
  generatedUtc:   string;
  headlineMethod: string;
  floorMethod:    string;
  caveat:         string;
  gates:          { yrfi: number; nrfi: number };
  kellyFraction:  number;
  startBank:      number;
  realStart:      string;
  projected:      RecSide | null;
  real:           RecSide | null;
}

/** Guard every .toFixed(). A missing field reads as undefined, and
 *  undefined.toFixed() is the crash this file exists to prevent. */
export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}


/** What the CURRENT model would stake on one game, at quarter-Kelly. */
export interface ReplayStake {
  action: "BET" | "SKIP";
  /** Compounded quarter-Kelly stake in units. Present only on BET. */
  stake?: number;
  /** Plain reason, present only on SKIP. */
  reason?: string;
  /** Price was the -125 stand-in rather than a captured DK number. */
  assumed?: boolean;
}

/** Key a board row the same way the exporter labels a game. */
export function replayKey(away: string, home: string, gameNumber: number | undefined,
                          side: string): string {
  const gn = gameNumber && gameNumber > 1 ? ` G${gameNumber}` : "";
  return `${away}@${home}${gn}|${side}`;
}

/**
 * Per-game quarter-Kelly stakes for one date.
 *
 * WHY (2026-07-28): the board's stake chip read `units_risked` off the
 * ledger, which is a flat 1.00 for every bet placed before Kelly went
 * live -- so browsing to April showed "staked 1.00u" on every row even
 * though the operator now sizes by Kelly. These are the SAME numbers the
 * day-reconcile table shows, so the two surfaces cannot disagree.
 *
 * Resolves against the REAL record first, then PROJECTED, because REAL
 * only starts 2026-05-07 and April exists only in the projected replay.
 */
export function replayStakesFor(
  rec: RecFile | null | undefined,
  date: string,
): Map<string, ReplayStake> {
  const out = new Map<string, ReplayStake>();
  if (!rec || !date) return out;
  const day =
    rec.real?.days.find((d) => d.date === date) ??
    rec.projected?.days.find((d) => d.date === date);
  if (!day) return out;
  for (const g of day.games) {
    const key = `${g.game}|${g.side}`;
    out.set(key, g.record.action === "BET"
      ? { action: "BET", stake: g.record.stake, assumed: g.record.assumed }
      : { action: "SKIP", reason: g.record.reason });
  }
  return out;
}


/** The system's result over an arbitrary date window.
 *
 *  EVERY P&L FIELD HERE IS BRANDED `CumulativeUnits` (2026-07-30), and
 *  that is not decoration -- `formatUnits()` refuses the brand at
 *  compile time, so `next build` fails rather than shipping a summed
 *  unit figure. See lib/units.ts for why the sum is not money. Reach
 *  for `bankStart`/`bankEnd` or the return derived from them. */
export interface ReplayWindow {
  from: string; to: string;
  bets: number; wins: number; losses: number;
  /** Quarter-Kelly P&L in units, at the bank the run had reached.
   *  Summed across days: NOT printable. `bankEnd - bankStart`. */
  pnl: CumulativeUnits;
  /** The scale-free figure. pnl as a fraction of the bank at window
   *  start -- the ONLY number that is not distorted by where the
   *  compounding run happened to be when the window opened.
   *
   *  !! DO NOT RENDER THIS FIELD. It is wrong and it has been wrong
   *  since it was written; it is kept only so that deleting it is a
   *  separate, reviewable change. `pnl` covers BOTH sides while
   *  `bankStart` is a YRFI-ONLY bank (the 2026-07-30 exporter fix
   *  stopped the simulation staking a side the system does not bet).
   *  Over the last seven days that lands on -7.7% where the bank says
   *  -5.91%, because one NRFI bet the bank never took is in the
   *  numerator. `real.days` holds 24 such bets season-wide.
   *
   *  Nothing reads it today -- RoiPanel computes its own from
   *  `yrfi.pnl / bankStart`, which is exact because the bank IS
   *  bankStart plus the YRFI P&L. Use `bankReturn(bankStart, bankEnd)`
   *  from lib/units, which cannot pick the wrong numerator. */
  pct: number | null;
  bankStart: number | null;
  bankEnd: number | null;
  /** Split by side, because only one of them is actually bet.
   *  `flat` is the same bets at one unit a bet, PER SIDE -- see the
   *  window-level `flatPnl` below for why per-side matters.
   *  Both P&L fields are summed across days, hence the brand. */
  yrfi: { bets: number; wins: number; pnl: CumulativeUnits; flat: CumulativeUnits };
  nrfi: { bets: number; wins: number; pnl: CumulativeUnits; flat: CumulativeUnits };
  /** THE EDGE, UNLEVERED -- this window at a flat 1 unit a bet, before
   *  Kelly compounding multiplies it. BOTH SIDES; use `yrfi.flat` when
   *  pairing it with a YRFI-only headline.
   *
   *  Added 2026-07-29 because the operator kept reading the levered
   *  figure as the system's performance. Showing them side by side is
   *  the difference between "the model is finding edge" and "the model
   *  is finding edge AND we are levering it hard" -- separate questions
   *  with separate risks. season_record.py's own footer: "Flat 1u is the
   *  edge. The Kelly line is that same edge levered, and it is real only
   *  while the hit rate holds."
   *
   *  2026-07-30 BUG FIX. This was summed from each day's `flatPnl`,
   *  which is a DAY-level total and therefore includes NRFI -- while
   *  every headline it sat beside is YRFI-only, because NRFI is not bet.
   *  Season-wide it printed +9.29u where the honest twin of a +144.85u
   *  YRFI headline is +12.30u; the -2.97u difference is 22 NRFI
   *  would-be bets. A figure captioned "the same bets, unlevered" that
   *  silently covered a DIFFERENT set of bets is precisely the defect
   *  this dashboard spent two days removing, reintroduced by the fix
   *  for it. Now summed per game from the same `action === "BET"` loop
   *  that produces `pnl`, so the two cannot describe different
   *  populations. */
  flatPnl: CumulativeUnits;
  /** Bets priced at the -125 stand-in because no DK price was ever
   *  captured. Load-bearing: change that assumption to -155 and the
   *  season's simulated bank falls from ~967u to ~641u. A figure built
   *  substantially on an assumed price has to say so on its face. */
  assumed: number;
}

/* ============================================================
   RE-BASED UNITS.  1 unit = 1% of bankroll, and the bankroll is
   always 100 units.  Operator decision, 2026-07-30.

   WHY ANY OF THIS EXISTS.  The replay in season_record.json compounds
   the unit COUNT: it opens a 100u bank in April and stakes a fixed
   FRACTION of whatever the bank has become, so by late July a routine
   bet is 10.00u because the bank is 217u.  Under the operator's model
   that same bet is 4.61u for everyone, because a unit is 1% of your
   own bank whatever your bank is.  The replay's raw figures describe a
   bankroll exactly one person could ever have had.

   THE CONSEQUENCE THAT BITES.  Raw per-day figures are NOT ADDABLE.
   Summing simPnl over the last seven days gives -13.17u; the bank
   actually moved 223.07 -> 209.89, which is -5.91%, i.e. -5.91u on a
   100u bank.  The naive sum is 2.2x the truth and it is the number
   every "just add the column" instinct produces.

   THE FIX, and it is one line of arithmetic: divide each day's P&L by
   the bank it OPENED with.  That ratio is bankroll-free -- it is what
   a $1k follower and a $25k follower both experienced -- and the
   ratios compound to exactly the bank ratio, so a chart drawn from
   them ends where the headline says it ends.  Verified: compounding
   the five daily returns gives -5.90u against the bank ratio's
   -5.91u, the gap being float rounding.

   WHICH SIDE.  `simBankAfter` stakes YRFI ONLY, as of the 2026-07-30
   exporter fix, because _LR_STRONG_NRFI_P = 1.01 has had NRFI
   switched off since 2026-06-07.  But `day.games` still CONTAINS
   NRFI rows -- 24 of them in the real-price window -- so anything
   that counts bets by walking games and anything that reads the bank
   are describing different populations unless the walk filters.  This
   is not hypothetical: `ReplayWindow.pct` folds NRFI into a figure it
   divides by a YRFI-only bank and lands on -7.7% where the bank says
   -5.91%.  Everything below reports YRFI and counts the off-side
   separately, so the two can never drift apart silently.
   ============================================================ */

/** One day, with its P&L expressed as a share of the bank it opened. */
export interface RebasedDay {
  date: string;
  /** P&L as a fraction of the opening bank. Bankroll-free. */
  ret: number;
  /** `ret` on a 100-unit bank -- the publishable per-night figure. */
  units: number;
  /** Bank indexed to 100 at window open, after this day settles. */
  indexed: number;
  /** The replay's own compounded figure. Kept for provenance only;
   *  never render it beside a re-based one without saying so. */
  rawPnl: number;
  bankBefore: number;
  bankAfter: number;
}

export interface RebasedWindow {
  from: string;
  to: string;
  days: RebasedDay[];
  /** The replay's compounded bank at each end of the window. */
  bankStart: number;
  bankEnd: number;
  /** THE HEADLINE: window return on a 100-unit bank. Derived from the
   *  bank ratio, never from a sum, so it cannot disagree with `days`. */
  units: number;
  /** The same figure as a fraction. */
  pct: number;
  /** YRFI only -- the side the bank actually stakes. */
  bets: number;
  wins: number;
  losses: number;
  /** Bets the record holds on a side the bank does NOT stake. Surfaced
   *  rather than dropped: a non-zero value means the record and the
   *  bank are describing different books and the reader should be told. */
  offSideBets: number;
  /** Of `bets`, how many were priced at the -125 stand-in. */
  assumed: number;
}

/**
 * The last `count` calendar days of a side's record, re-based.
 *
 * CALENDAR days, not the last N ENTRIES. `side.days` only holds dates
 * that had games, so "the last 7 entries" silently reaches back 9 or 10
 * days on a sparse stretch while the label still says a week. The
 * returned `from`/`to` are the real span so a caption can state it.
 */
export function rebaseLastDays(
  side: RecSide | null | undefined,
  count: number,
): RebasedWindow | null {
  if (!side || side.days.length === 0 || count < 1) return null;

  // A null simBankAfter means nothing settled that day; it carries no
  // bank observation, so it cannot be a point on a bank curve.
  const withBank = side.days.filter((d) => isNum(d.simBankAfter) && isNum(d.simPnl));
  if (withBank.length === 0) return null;

  const last = withBank[withBank.length - 1];
  const endMs = Date.parse(`${last.date}T00:00:00Z`);
  if (!Number.isFinite(endMs)) return null;
  const startMs = endMs - (count - 1) * 86_400_000;
  const startIso = new Date(startMs).toISOString().slice(0, 10);

  const win = withBank.filter((d) => d.date >= startIso);
  if (win.length === 0) return null;

  const days: RebasedDay[] = [];
  let indexed = 100;
  for (const d of win) {
    const bankAfter = d.simBankAfter as number;
    const rawPnl = d.simPnl;
    const bankBefore = bankAfter - rawPnl;
    // A non-positive opening bank makes the ratio meaningless (and the
    // replay is ruined anyway); treat the day as flat rather than
    // emitting an Infinity that would blow up the chart's scale.
    const ret = bankBefore > 0 ? rawPnl / bankBefore : 0;
    indexed *= 1 + ret;
    days.push({
      date: d.date, ret, units: ret * 100,
      indexed, rawPnl, bankBefore, bankAfter,
    });
  }

  const bankStart = days[0].bankBefore;
  const bankEnd = days[days.length - 1].bankAfter;
  // FROM THE RATIO, NOT FROM A SUM. This is the whole point of the
  // module: `units` and the `indexed` series are two readings of one
  // quantity, so the curve's last point IS the headline.
  const pct = bankStart > 0 ? bankEnd / bankStart - 1 : 0;

  let bets = 0, wins = 0, offSideBets = 0, assumed = 0;
  for (const d of win) {
    for (const g of d.games) {
      if (g.record.action !== "BET") continue;
      if (g.side === "NRFI") { offSideBets += 1; continue; }
      bets += 1;
      if (g.record.win === true) wins += 1;
      if (g.record.assumed) assumed += 1;
    }
  }

  return {
    from: days[0].date, to: days[days.length - 1].date,
    days, bankStart, bankEnd,
    units: pct * 100, pct,
    bets, wins, losses: bets - wins, offSideBets, assumed,
  };
}


/**
 * What the system did between two dates.
 *
 * TWO THINGS THIS MUST EXPOSE, both learned the hard way on 2026-07-28:
 *
 *  1. `pct`. The replay compounds from 100u in April, so by late July the
 *     bank is ~316u and a normal week prints as "-58.85u" -- a number
 *     that reads like a catastrophe against the operator's real 100u
 *     bankroll, where the same week is about -16u. Units are meaningless
 *     without the bank they were staked from; the percentage is not.
 *  2. The YRFI/NRFI split. NRFI betting is disabled live
 *     (_LR_STRONG_NRFI_P = 1.01), so NRFI rows in the record are TRACKED,
 *     NOT BET. Folding them into one figure reports a loss on bets that
 *     would never have been placed.
 */
export function replayWindow(
  side: RecSide | null | undefined,
  startIso: string | undefined,
  endIso: string | undefined,
): ReplayWindow | null {
  if (!side || !startIso || !endIso) return null;
  const days = side.days.filter((d) => d.date >= startIso && d.date <= endIso);
  if (days.length === 0) return null;
  let bets = 0, wins = 0, pnl = 0, assumed = 0, flatPnl = 0;
  const y = { bets: 0, wins: 0, pnl: 0, flat: 0 };
  const n = { bets: 0, wins: 0, pnl: 0, flat: 0 };
  // Accumulated as plain numbers, branded once on the way out --
  // asCumulative() at the return is the single place the "this was
  // summed across days" fact gets attached.
  for (const d of days) {
    for (const g of d.games) {
      if (g.record.action !== "BET") continue;
      const p = g.record.pnl ?? 0;
      const w = g.record.win === true;
      bets += 1; if (w) wins += 1; pnl += p;
      if (g.record.assumed) assumed += 1;
      // FLAT, PER GAME, in the same loop as pnl -- see flatPnl's note.
      // A win pays the price; a loss costs exactly one unit.
      const odds = g.record.odds;
      const payout = isNum(odds)
        ? (odds > 0 ? odds / 100 : 100 / -odds)
        : 0;
      const flat = w ? payout : -1;
      flatPnl += flat;
      const bucket = g.side === "NRFI" ? n : y;
      bucket.bets += 1; if (w) bucket.wins += 1; bucket.pnl += p;
      bucket.flat += flat;
    }
  }
  if (bets === 0) return null;
  const first = days[0], last = days[days.length - 1];
  const bankStart = isNum(first.simBankAfter) && isNum(first.simPnl)
    ? first.simBankAfter - first.simPnl : null;
  return {
    from: first.date, to: last.date,
    bets, wins, losses: bets - wins, pnl: asCumulative(pnl),
    pct: isNum(bankStart) && bankStart > 0 ? pnl / bankStart : null,
    bankStart,
    bankEnd: isNum(last.simBankAfter) ? last.simBankAfter : null,
    yrfi: { ...y, pnl: asCumulative(y.pnl), flat: asCumulative(y.flat) },
    nrfi: { ...n, pnl: asCumulative(n.pnl), flat: asCumulative(n.flat) },
    assumed, flatPnl: asCumulative(flatPnl),
  };
}
