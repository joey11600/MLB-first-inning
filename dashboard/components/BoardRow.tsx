"use client";

import type { BoardRow, GameDetail, PickSide, PickStrength } from "@/lib/types";
import { GameDetails } from "./GameDetails";
import styles from "./BoardRow.module.css";

const SEGMENT_COUNT = 10;

function toneClass(side: PickSide, strength: PickStrength): string {
  if (side === "NRFI") return strength === "STRONG" ? "nrfiStrong" : "nrfiLean";
  if (side === "YRFI") return strength === "STRONG" ? "yrfiStrong" : "yrfiLean";
  return "passTone";
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
          {row.rank.toString().padStart(2, "0")}
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
          {detail?.gradedResult && (
            <ResultBadge
              graded={detail.gradedResult}
              actual={detail.actualSide}
              awayRuns={detail.fiAwayRuns}
              homeRuns={detail.fiHomeRuns}
              totalRuns={detail.fiTotalRuns}
            />
          )}
        </span>

        <span className={`num ${styles.lambdaVal} ${styles.right}`}>
          {(row.yrfiPct / 100).toFixed(2)}
        </span>

        <span className={styles.pickCell}>
          <span className={styles.pickPill}>
            <span className={styles.pickDot} aria-hidden />
            <span className={styles.pickLabel}>
              {row.pickSide === "PASS"
                ? row.pickStrength === "NO DATA"
                  ? "NO DATA"
                  : "PASS"
                : `${row.pickStrength} ${row.pickSide}`}
            </span>
          </span>
        </span>

        <span className={styles.meterCell}>
          <SegmentedBar yrfiProb={row.yrfiPct / 100} side={row.pickSide} />
        </span>

        <span className={`num ${styles.pct} ${styles.right}`}>
          <Bar pct={row.nrfiPct} tone="nrfi" />
          {row.nrfiPct.toFixed(1)}
        </span>

        <span className={`num ${styles.pct} ${styles.right}`}>
          <Bar pct={row.yrfiPct} tone="yrfi" />
          {row.yrfiPct.toFixed(1)}
        </span>

        <span className={styles.caret} aria-hidden>
          ⌄
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

function Bar({ pct, tone }: { pct: number; tone: "nrfi" | "yrfi" }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <span className={`${styles.inlineBar} ${styles[tone]}`}>
      <span className={styles.inlineBarFill} style={{ width: `${p}%` }} />
    </span>
  );
}

function SegmentedBar({
  yrfiProb,
  side,
}: {
  yrfiProb: number;
  side: PickSide;
}) {
  const p = Math.max(0, Math.min(1, yrfiProb));
  const markerIdx = Math.min(SEGMENT_COUNT - 1, Math.floor(p * SEGMENT_COUNT));

  return (
    <span
      className={styles.segBar}
      role="img"
      aria-label={`P(YRFI) ${(p * 100).toFixed(1)}%`}
    >
      {Array.from({ length: SEGMENT_COUNT }).map((_, i) => {
        const filled =
          side === "NRFI" ? i <= markerIdx
          : side === "YRFI" ? i >= markerIdx
          : i === markerIdx;
        const cls = [
          styles.segBarSeg,
          filled ? styles.segBarSegFill : "",
          i === markerIdx ? styles.segBarSegMarker : "",
        ].filter(Boolean).join(" ");
        return <span key={i} className={cls} />;
      })}
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
