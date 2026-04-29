export type PickSide = "NRFI" | "YRFI" | "PASS";
export type PickStrength =
  | "STRONG"
  | "LEAN"
  | "NO EDGE"
  | "NO DATA"
  | "STARTER PENDING";
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
  fiEra: number | null;
  fiWhip: number | null;
  fiIp: number | null;
  quality: DataQuality;
}

export interface OffenseStats {
  obp: number | null;
  slg: number | null;
  rpg: number | null;
  quality: DataQuality;
}

export type GradedResult = "WIN" | "LOSS" | "PASS" | "POSTPONED" | "SUSPENDED" | null;
export type ActualSide   = "NRFI" | "YRFI" | "POSTPONED" | "SUSPENDED" | null;

export interface GameDetail {
  gamePk: string;
  gameTimeEt: string;
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
  away: {
    team: string;
    pitcher: PitcherStats;
    offense: OffenseStats;
  };
  home: {
    team: string;
    pitcher: PitcherStats;
    offense: OffenseStats;
  };
}

export interface BoardResponse {
  date: string;              // YYYY-MM-DD
  availableDates: string[];  // sorted desc
  rows: BoardRow[];
  details: Record<string, GameDetail>;  // keyed by "AWAY@HOME"
  generatedAt: string | null;
}
