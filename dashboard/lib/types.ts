export type PickSide = "NRFI" | "YRFI" | "PASS";
export type PickStrength =
  | "STRONG"
  | "LEAN"
  | "NO EDGE"
  | "NO DATA"
  | "STARTER PENDING"
  | "LINEUP PENDING"
  | "LOW LAMBDA";
export type DataQuality = "live" | "ltd" | "sm" | "avg" | "";

export interface BoardRow {
  rank: number;
  away: string;
  home: string;
  lambda: number;
  pickSide: PickSide;
  pickStrength: PickStrength;
  pickLabel: string;
  nrfiPct: number;
  yrfiPct: number;
  // Doubleheader disambiguation (added later -- old CSVs may not have these)
  gamePk: string;        // empty when row is from a pre-2026-04 board CSV
  gameNumber: number;    // 1 by default
  doubleHeader: string;  // "N" / "Y" / "S"
  gameTimeEt: string;    // mirrors picks_2026.csv game_time_et for the same row
  // T3.17: shadow-tracking v3 calibrator verdict (Variant K).  Present
  // for rows where pick_variants.K has been computed; absent for pre-K
  // dates or rows that pre-date the nrfi_prob_raw column.  When present,
  // the dashboard's model toggle can switch to display v3 fields.
  v3?: {
    pickSide:        PickSide;
    pickStrength:    PickStrength;
    pickLabel:       string;
    nrfiPct:         number;
    yrfiPct:         number;
    /** True when v3 disagrees with v2 (different side or PASS-vs-bet).
     *  Drives a small badge on the row. */
    disagreesWithV2: boolean;
  };
}

export interface PitcherStats {
  name: string;
  mlbId: number | null;        // null = unknown / TBD / older row without id
  era: number | null;
  whip: number | null;
  fip: number | null;
  bb9: number | null;
  hr9: number | null;
  k9: number | null;
  quality: DataQuality;
}

export interface OffenseStats {
  obp: number | null;
  slg: number | null;
  rpg: number | null;
  quality: DataQuality;
}

/** A single LR feature contribution -- the model's "why this pick?" reason.
 *  Sign convention: positive = pushes toward NRFI (less expected scoring),
 *  negative = pushes toward YRFI.  Magnitude is the signed `w*(x-mean)/std`
 *  z-contribution, comparable across features.  Per-half (t1, b1).
 */
export interface FactorContribution {
  name:         string;   // feature name from _T1/B1_EXPECTED_FEATURES
  value:        number;   // raw feature value for this game
  contribution: number;   // signed contribution to that half's logit
}

/** A single batter in the top-3 of the lineup that the opposing pitcher will face.
 *  Source: backtest.current_season_top3_per_batter (lineup-aware, season-to-date stats).
 *  All stat fields are nullable since callups / early-April players may have no log yet. */
export interface BatterLine {
  id:   number;
  name: string;
  bats: "L" | "R" | "S" | "";
  obp:  number | null;
  slg:  number | null;
  iso:  number | null;
  ab:   number | null;
}

export type GradedResult = "WIN" | "LOSS" | "PASS" | "POSTPONED" | "SUSPENDED" | null;
export type ActualSide   = "NRFI" | "YRFI" | "POSTPONED" | "SUSPENDED" | null;

export interface GameDetail {
  gamePk: string;
  gameTimeEt: string;
  /** Top-N LR feature contributions for the T1 / B1 halves.  Empty
   *  array on rows from before the predictor started persisting them. */
  topFactorsT1?: FactorContribution[];
  topFactorsB1?: FactorContribution[];
  parkFactor: number | null;
  awayProj: number | null;
  homeProj: number | null;
  combinedLambda: number | null;
  // LR-v3 derived expected-runs-per-half for the "Slate Projections" view
  lambdaLrT1: number | null;     // home pitcher's half (T1)
  lambdaLrB1: number | null;     // away pitcher's half (B1)
  lambdaLrTotal: number | null;  // sum, for the screenshot's "Projected Runs 1st Inning" col
  overProb: number | null;
  underProb: number | null;
  blendedInputs: number | null;
  // Grading (filled by tracker.py --grade; null when not yet graded)
  actualSide:    ActualSide;
  gradedResult:  GradedResult;
  fiAwayRuns:    number | null;
  fiHomeRuns:    number | null;
  fiTotalRuns:   number | null;
  // Live market odds + edge (populated by --import-odds; null when no odds yet)
  marketNrfiOdds: string;        // American format, e.g. "+115"
  marketYrfiOdds: string;
  sportsbook:     string;        // e.g. "DraftKings"
  oddsCapturedAt: string;        // ISO timestamp; empty when no odds
  edgeOnPick:     number | null; // model NRFI/YRFI prob - implied prob for picked side
  betPlaced:      "" | "Y" | "N";// Y = edge >= min_edge; N = below threshold; "" = no odds
  unitsRisked:    number | null;
  profitLossUnits: number | null;
  // T2.54: opening odds + CLV for line-drift display.  Captured the
  // first time we saw odds for this game; the delta from open to close
  // tells the user whether the market is moving toward or away from
  // their pick (and quantifies it as CLV in pp).
  openedNrfiOdds:   string;      // American format; "" when never captured
  openedYrfiOdds:   string;
  openedCapturedAt: string;      // ISO timestamp; empty when no opened odds
  clvPct:           number | null; // (close implied prob - open implied prob) on the picked side
  // T3.17: v3 shadow grading (Variant K).  Outcome of v3's verdict on
  // this game.  Present alongside v2's grading when pick_variants.K
  // has a row for this game; absent otherwise.
  v3?: {
    pickSide:        PickSide;
    pickStrength:    PickStrength;
    pickLabel:       string;
    nrfiPct:         number;
    yrfiPct:         number;
    gradedResult:    GradedResult;
    unitsRisked:     number | null;
    profitLossUnits: number | null;
    disagreesWithV2: boolean;
  };
  away: {
    team: string;
    pitcher: PitcherStats;
    offense: OffenseStats;
    /** Top-3 of the away team's batting order this game.  This is the
     *  lineup the HOME pitcher faces in T1 (top of the 1st).  Empty when
     *  MLB hasn't published the lineup yet (typical pre-game state). */
    lineup:  BatterLine[];
  };
  home: {
    team: string;
    pitcher: PitcherStats;
    offense: OffenseStats;
    /** Top-3 of the home team's batting order this game.  This is the
     *  lineup the AWAY pitcher faces in B1 (bottom of the 1st). */
    lineup:  BatterLine[];
  };
}

/** A single intraday pick flip surfaced from data/pick_changes.csv.
 *  Logged by tracker.log_picks() whenever a pre-game pick label changes
 *  (e.g. STARTER PENDING -> STRONG YRFI as the lineup posts and the
 *  model can finally see top-3 OBP). */
export interface PickChange {
  capturedAtUtc: string;     // ISO timestamp the change was recorded
  date:          string;     // slate date (YYYY-MM-DD)
  gamePk:        string;
  awayTeam:      string;
  homeTeam:      string;
  gameTimeEt:    string;     // "7:10 PM ET"
  oldPickLabel:  string;     // e.g. "PASS - Starter pending"
  newPickLabel:  string;     // e.g. "STRONG YRFI"
}

/** Classification thresholds the predictor uses, surfaced to the dashboard
 *  so the tentative-pick classifier in BoardRow.tsx never drifts from the
 *  Python source of truth.  Fall back to default constants if the response
 *  is missing this field (older deploys). */
export interface PickThresholds {
  strongNrfiP:    number;   // NRFI prob >= this -> STRONG NRFI
  leanNrfiP:      number;   // NRFI prob >= this -> LEAN NRFI
  passLoP:        number;   // NRFI prob >= this -> PASS NO EDGE
  leanYrfiP:      number;   // NRFI prob >= this (and below passLoP) -> LEAN YRFI
  lambdaYrfiFloor: number;  // would-be YRFI demoted to LOW LAMBDA when below
}

export interface BoardResponse {
  date: string;              // YYYY-MM-DD
  availableDates: string[];  // sorted desc
  rows: BoardRow[];
  details: Record<string, GameDetail>;  // keyed by "AWAY@HOME"
  generatedAt: string | null;
  /** Today's pre-game pick flips, newest first.  Empty when no changes
   *  yet today (most common case at slate generation). */
  pickChanges: PickChange[];
  /** Classifier thresholds shared from the Python predictor.  Optional
   *  for back-compat with cached responses; the BoardRow tentative
   *  classifier falls back to hardcoded defaults if missing. */
  thresholds?: PickThresholds;
}
