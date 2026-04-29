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
  if (side === "PASS") return strength === "NO DATA" ? "NO DATA" : "PASS";
  return `${strength} ${side}`;
}

// "2:10 PM ET" -> "2:10 PM"; missing/empty -> "—"
function formatGameTime(s: string): string {
  if (!s) return "—";
  return s.replace(/\s*ET\s*$/, "").trim();
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

        <span className={`num ${styles.rank}`}>
          {formatGameTime(row.gameTimeEt || detail?.gameTimeEt || "")}
        </span>

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

        <span className={`num ${styles.lambdaVal} ${styles.right}`}>
          {(row.yrfiPct / 100).toFixed(2)}
        </span>

        <span className={styles.pickCell}>
          <span
            className={`${styles.pickPill} ${
              row.pickStrength === "STARTER PENDING" ? styles.pickPillPending : ""
            }`}
          >
            <span className={styles.pickDot} aria-hidden />
            <span className={styles.pickLabel}>
              {pickLabelText(row.pickSide, row.pickStrength)}
            </span>
          </span>
          <OddsChip row={row} detail={detail} />
        </span>

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

        <span className={styles.expander} aria-hidden>
          <span className={styles.expanderLabel}>
            {expanded ? "Hide" : "Details"}
          </span>
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

/** Live-odds chip: shows the sportsbook + price + edge for the picked side.
 *  Hidden entirely when no odds have been imported for the row.  Coloring:
 *   - bet=Y: green tint (positive edge above threshold)
 *   - bet=N: muted (negative edge or below threshold; we skipped the bet)
 *   - "" (no odds yet): renders nothing
 *  PASS picks always render nothing -- there's no side to price. */
function OddsChip({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  if (!detail) return null;
  if (row.pickSide === "PASS") return null;

  const price = oddsForPick(row.pickSide, detail.marketNrfiOdds, detail.marketYrfiOdds);
  if (!price) return null;

  const edgePct = detail.edgeOnPick != null ? detail.edgeOnPick * 100 : null;
  const edgeStr =
    edgePct == null
      ? ""
      : edgePct >= 0
        ? `+${edgePct.toFixed(1)}%`
        : `${edgePct.toFixed(1)}%`;

  const bet = detail.betPlaced;
  const cls = bet === "Y" ? styles.oddsBet : bet === "N" ? styles.oddsSkip : "";
  const book = shortBook(detail.sportsbook);

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
      {edgeStr && (
        <>
          <span className={styles.oddsSep}>·</span>
          <span className={styles.oddsEdge}>{edgeStr}</span>
        </>
      )}
    </span>
  );
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
