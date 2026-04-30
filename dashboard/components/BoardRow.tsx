"use client";

import type { BoardRow, GameDetail, PickSide, PickStrength } from "@/lib/types";
import { LambdaMeter } from "./LambdaMeter";
import { GameDetails } from "./GameDetails";
import styles from "./BoardRow.module.css";

function toneClass(side: PickSide, strength: PickStrength): string {
  if (side === "NRFI") return strength === "STRONG" ? "nrfiStrong" : "nrfiLean";
  if (side === "YRFI") return strength === "STRONG" ? "yrfiStrong" : "yrfiLean";
  return "passTone";
}

function pickLabelText(side: PickSide, strength: PickStrength): string {
  if (strength === "STARTER PENDING") return "STARTER PENDING";
  if (strength === "LINEUP PENDING")  return "LINEUP PENDING";
  if (strength === "LOW LAMBDA")      return "PASS · LOW λ";
  if (side === "PASS") return strength === "NO DATA" ? "NO DATA" : "PASS";
  return `${strength} ${side}`;
}

// "2:10 PM ET" -> "2:10 PM"; missing/empty -> "—"
function formatGameTime(s: string): string {
  if (!s) return "—";
  return s.replace(/\s*ET\s*$/, "").trim();
}

/** True for non-numeric time placeholders the predictor emits when MLB
 *  hasn't published a real start time yet -- e.g. "After Game 1" for the
 *  back end of a traditional doubleheader.  These get a chip treatment
 *  (dashed muted pill, parallel to STARTER/LINEUP PENDING) instead of
 *  the regular mono time, so they (a) read as tentative not as a real
 *  time, and (b) fit in the narrow 82px time column without overflowing
 *  into the matchup cell. */
function isPlaceholderTime(s: string): boolean {
  if (!s || s === "—") return false;
  return !s.includes(":");
}

// Short sportsbook name for the live-odds chip ("DraftKings" -> "DK")
function shortBook(name: string): string {
  if (!name) return "";
  const n = name.trim().toLowerCase();
  if (n.startsWith("draftking"))   return "DK";
  if (n.startsWith("fanduel"))     return "FD";
  if (n.startsWith("betmgm"))      return "MGM";
  if (n.startsWith("caesars"))     return "CAE";
  if (n.startsWith("pinnacle"))    return "PIN";
  return name.slice(0, 3).toUpperCase();
}

/** Tooltip for the P(YRFI) cell -- gives the user a peek at WHY two
 *  games with similar YRFI% can land in different pick zones.  Combined
 *  λ is the model's raw expected first-inning runs; if it's below the
 *  0.78 floor the predictor demotes a would-be YRFI pick to PASS. */
function lambdaTooltip(row: BoardRow): string {
  const lam = row.lambda.toFixed(2);
  const yrfi = row.yrfiPct.toFixed(1);
  const floor = "0.78";
  const note =
    row.lambda < 0.78
      ? `\n• Below 0.78 floor → would-be YRFI demoted to PASS - LOW λ`
      : `\n• Above 0.78 floor → YRFI bets enabled when YRFI% high enough`;
  return `Combined λ ${lam} (expected total 1st-inning runs)\nP(YRFI) ${yrfi}%${note}`;
}

// Pick the right odds (NRFI vs YRFI) for the picked side, normalize sign.
function oddsForPick(side: PickSide, nrfi: string, yrfi: string): string {
  const raw = (side === "NRFI" ? nrfi : side === "YRFI" ? yrfi : "").trim();
  if (!raw) return "";
  // Already has +/- prefix?  Otherwise prepend +.
  return /^[+\-]/.test(raw) ? raw : (Number.parseFloat(raw) > 0 ? `+${raw}` : raw);
}

export function BoardRowItem({
  row,
  detail,
  expanded,
  onToggle,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
  expanded: boolean;
  onToggle: () => void;
}) {
  const tone = toneClass(row.pickSide, row.pickStrength);

  return (
    <div className={`${styles.row} ${styles[tone]} ${expanded ? styles.open : ""}`}>
      <button
        type="button"
        className={styles.clickable}
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={`details-${row.away}-${row.home}`}
      >
        <span className={styles.spine} aria-hidden />

        <TimeCell value={formatGameTime(row.gameTimeEt || detail?.gameTimeEt || "")} />

        <span className={styles.matchup}>
          <span className={styles.team}>{row.away}</span>
          <span className={styles.at}>at</span>
          <span className={styles.team}>{row.home}</span>
          {row.doubleHeader && row.doubleHeader !== "N" && (
            <span className={styles.dhTag} title={`Doubleheader Game ${row.gameNumber}`}>
              DH-{row.gameNumber}
            </span>
          )}
        </span>

        <span className={styles.resultCell}>
          {detail?.gradedResult ? (
            <ResultBadge
              graded={detail.gradedResult}
              actual={detail.actualSide}
              awayRuns={detail.fiAwayRuns}
              homeRuns={detail.fiHomeRuns}
              totalRuns={detail.fiTotalRuns}
            />
          ) : (
            <span className={styles.resultPending}>—</span>
          )}
        </span>

        <span
          className={`num ${styles.lambdaVal} ${styles.right}`}
          title={lambdaTooltip(row)}
        >
          {(row.yrfiPct / 100).toFixed(2)}
        </span>

        <span className={styles.pickCell}>
          <span
            className={`${styles.pickPill} ${
              (row.pickStrength === "STARTER PENDING"
                || row.pickStrength === "LINEUP PENDING")
                ? styles.pickPillPending
                : ""
            } ${row.pickStrength === "LOW LAMBDA" ? styles.pickPillLowLambda : ""}`}
            title={
              row.pickStrength === "LOW LAMBDA"
                ? `Demoted from STRONG/LEAN YRFI: combined λ ${row.lambda.toFixed(2)} below the 0.78 floor (model expects too few total runs to bet YRFI confidently). Tested in backtest: floor adds ~+1.36u/season.`
                : undefined
            }
          >
            <span className={styles.pickDot} aria-hidden />
            <span className={styles.pickLabel}>
              {pickLabelText(row.pickSide, row.pickStrength)}
            </span>
          </span>
          <OddsChip row={row} detail={detail} />
        </span>

        <EdgeCell row={row} detail={detail} />

        <span className={styles.meterCell}>
          <LambdaMeter yrfiProb={row.yrfiPct / 100} compact />
        </span>

        <span className={`num ${styles.pct} ${styles.right}`}>
          <Bar pct={row.nrfiPct} tone="nrfi" />
          {row.nrfiPct.toFixed(1)}
        </span>

        <span className={`num ${styles.pct} ${styles.right}`}>
          <Bar pct={row.yrfiPct} tone="yrfi" />
          {row.yrfiPct.toFixed(1)}
        </span>

        <span
          className={styles.expander}
          aria-hidden
          title={expanded ? "Hide details" : "Show details"}
        >
          <span className={`${styles.caret} ${expanded ? styles.caretOpen : ""}`}>
            ⌄
          </span>
        </span>
      </button>

      {expanded && (
        <div id={`details-${row.away}-${row.home}`} className={styles.detailsWrap}>
          <GameDetails row={row} detail={detail} />
        </div>
      )}
    </div>
  );
}

/** Live-odds chip: sportsbook + price for the picked side.  Edge is shown
 *  in its own dedicated column to the right (see EdgeCell).  Hidden when
 *  no odds yet; PASS picks always render nothing. */
function OddsChip({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  if (!detail) return null;
  if (row.pickSide === "PASS") return null;
  const price = oddsForPick(row.pickSide, detail.marketNrfiOdds, detail.marketYrfiOdds);
  if (!price) return null;

  const bet  = detail.betPlaced;
  const cls  = bet === "Y" ? styles.oddsBet : bet === "N" ? styles.oddsSkip : "";
  const book = shortBook(detail.sportsbook);
  const edgePct = detail.edgeOnPick != null ? detail.edgeOnPick * 100 : null;
  const edgeStr = edgePct == null ? "" : (edgePct >= 0 ? `+${edgePct.toFixed(1)}%` : `${edgePct.toFixed(1)}%`);

  return (
    <span
      className={`${styles.oddsChip} ${cls}`}
      title={
        bet === "Y"
          ? `Bet placed: ${row.pickSide} @ ${price} (edge ${edgeStr})`
          : bet === "N"
            ? `Skipped: edge ${edgeStr || "below threshold"} on ${row.pickSide} @ ${price}`
            : `${row.pickSide} @ ${price}${edgeStr ? ` (edge ${edgeStr})` : ""}`
      }
    >
      {book && <span className={styles.oddsBook}>{book}</span>}
      <span className={styles.oddsPrice}>{price}</span>
    </span>
  );
}


/** Dedicated EDGE column.  Right-aligned percentage with sign-aware tint:
 *   positive (good bet) → warm-brown (--primary)
 *   negative (skip)     → muted-red
 *   no odds / PASS pick → em-dash, low contrast */
function EdgeCell({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  // PASS picks: no side to price an edge against.
  if (row.pickSide === "PASS" || !detail || detail.edgeOnPick == null) {
    return <span className={`${styles.edgeCell} ${styles.edgeNone}`}>—</span>;
  }
  const pct = detail.edgeOnPick * 100;
  const cls = pct > 0 ? styles.edgePos : pct < 0 ? styles.edgeNeg : "";
  const sign = pct >= 0 ? "+" : "";
  return (
    <span
      className={`${styles.edgeCell} ${cls}`}
      title={`Model edge over implied prob: ${sign}${pct.toFixed(2)}%`}
    >
      {sign}{pct.toFixed(1)}%
    </span>
  );
}


/** Time column renderer.  Real wall-clock times ("12:35 PM") use the
 *  monospace .rank treatment for a tabular feel down the column.  When
 *  MLB hasn't published a real start (DH-Y game-2 placeholder, TBD,
 *  etc.) the predictor emits a non-numeric string -- we render that as
 *  a small dashed chip so the column rhythm isn't broken AND the chip
 *  visually telegraphs "this is a tentative state, not a real time". */
function TimeCell({ value }: { value: string }) {
  if (isPlaceholderTime(value)) {
    // Display abbreviation: "After Game 1" -> "After G1" so the chip fits
    // inside the 82px time column at its uppercase 9.5px sans tracking.
    // The full phrase stays in the tooltip for clarity.
    const display = value.replace(/\bGame\s+(\d+)\b/i, "G$1");
    return (
      <span
        className={styles.rankTag}
        title={`${value} — start time pending until prior game finishes`}
      >
        <span className={styles.rankTagDot} aria-hidden />
        {display}
      </span>
    );
  }
  return <span className={`num ${styles.rank}`}>{value}</span>;
}


function Bar({ pct, tone }: { pct: number; tone: "nrfi" | "yrfi" }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <span className={`${styles.inlineBar} ${styles[tone]}`}>
      <span className={styles.inlineBarFill} style={{ width: `${p}%` }} />
    </span>
  );
}


function ResultBadge({
  graded,
  actual,
  awayRuns,
  homeRuns,
  totalRuns,
}: {
  graded:    NonNullable<GameDetail["gradedResult"]>;
  actual:    GameDetail["actualSide"];
  awayRuns:  number | null;
  homeRuns:  number | null;
  totalRuns: number | null;
}) {
  // Box-score format: away-home, e.g. "0-2".  Falls back to total runs when
  // we only have an aggregate (older grading data).
  const score =
    awayRuns != null && homeRuns != null
      ? `${awayRuns}-${homeRuns}`
      : totalRuns != null
        ? `${totalRuns}R`
        : "-";
  const totalText = totalRuns != null ? `${totalRuns}R` : "-";

  // Postponed / suspended games: amber pause indicator
  if (graded === "POSTPONED" || graded === "SUSPENDED") {
    return (
      <span className={`${styles.resultBadge} ${styles.resultPP}`} title={graded}>
        <span className={styles.resultGlyph}>PP</span>
      </span>
    );
  }
  // PASS picks (model said no edge) -- show the actual box score muted
  if (graded === "PASS") {
    return (
      <span
        className={`${styles.resultBadge} ${styles.resultPass}`}
        title={`PASS · 1st inning ${score} · ${actual ?? "-"} (${totalText})`}
      >
        <span className={styles.resultGlyph}>{score}</span>
      </span>
    );
  }
  // Real bet outcomes
  const isWin = graded === "WIN";
  const cls   = isWin ? styles.resultWin : styles.resultLoss;
  const glyph = isWin ? "W" : "L";
  return (
    <span
      className={`${styles.resultBadge} ${cls}`}
      title={`${graded} · 1st inning ${score} · ${actual ?? "-"} (${totalText})`}
    >
      <span className={styles.resultGlyph}>{glyph}</span>
      <span className={styles.resultRuns}>{score}</span>
    </span>
  );
}
