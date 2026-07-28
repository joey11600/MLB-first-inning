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


/** The replay's result over an arbitrary date window. */
export interface ReplayWindow {
  from: string; to: string;
  bets: number; wins: number; losses: number;
  /** Quarter-Kelly P&L over the window, in units. */
  pnl: number;
  bankStart: number | null;
  bankEnd: number | null;
}

/**
 * What the CURRENT model would have done between two dates.
 *
 * WHY (2026-07-28): the window toggle drove only the ledger card, and
 * the replay card is season-to-date by construction. So selecting
 * "Last 7d" showed the OLD system's real -8.63u with nothing anywhere
 * on the page answering "and what would the new model have done over
 * those same seven days?". The operator asked exactly that question.
 */
export function replayWindow(
  side: RecSide | null | undefined,
  startIso: string | undefined,
  endIso: string | undefined,
): ReplayWindow | null {
  if (!side || !startIso || !endIso) return null;
  const days = side.days.filter((d) => d.date >= startIso && d.date <= endIso);
  if (days.length === 0) return null;
  let bets = 0, wins = 0, pnl = 0;
  for (const d of days) {
    for (const g of d.games) {
      if (g.record.action !== "BET") continue;
      bets += 1;
      if (g.record.win) wins += 1;
      pnl += g.record.pnl ?? 0;
    }
  }
  if (bets === 0) return null;
  const first = days[0], last = days[days.length - 1];
  const bankStart = isNum(first.simBankAfter) && isNum(first.simPnl)
    ? first.simBankAfter - first.simPnl : null;
  return {
    from: first.date, to: last.date,
    bets, wins, losses: bets - wins,
    pnl,
    bankStart,
    bankEnd: isNum(last.simBankAfter) ? last.simBankAfter : null,
  };
}
