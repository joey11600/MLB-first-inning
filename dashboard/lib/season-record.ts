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
