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

function markerFor(side: PickSide, strength: PickStrength): string {
  if (side === "PASS") return "—";
  if (strength === "STRONG") return "⬥⬥";
  return "⬥";
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
              runs={detail.fiTotalRuns}
            />
          )}
        </span>

        <span className={`num ${styles.lambdaVal} ${styles.right}`}>
          {(row.yrfiPct / 100).toFixed(2)}
        </span>

        <span className={styles.pickCell}>
          <span className={styles.marker}>{markerFor(row.pickSide, row.pickStrength)}</span>
          <span className={styles.pickLabel}>
            {row.pickSide === "PASS"
              ? row.pickStrength === "NO DATA"
                ? "NO DATA"
                : "PASS"
              : `${row.pickStrength} ${row.pickSide}`}
          </span>
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

function ResultBadge({
  graded,
  actual,
  runs,
}: {
  graded: NonNullable<GameDetail["gradedResult"]>;
  actual: GameDetail["actualSide"];
  runs:   number | null;
}) {
  // Postponed / suspended games: amber pause indicator
  if (graded === "POSTPONED" || graded === "SUSPENDED") {
    return (
      <span className={`${styles.resultBadge} ${styles.resultPP}`} title={graded}>
        <span className={styles.resultGlyph}>PP</span>
      </span>
    );
  }
  // PASS picks (model said no edge) -- show actual outcome muted
  if (graded === "PASS") {
    const runText = runs != null ? runs.toString() : "-";
    return (
      <span className={`${styles.resultBadge} ${styles.resultPass}`} title={`PASS · 1st ${runText}R · ${actual ?? "-"}`}>
        <span className={styles.resultGlyph}>{actual === "NRFI" ? "0R" : `${runText}R`}</span>
      </span>
    );
  }
  // Real bet outcomes
  const isWin = graded === "WIN";
  const cls   = isWin ? styles.resultWin : styles.resultLoss;
  const glyph = isWin ? "W" : "L";
  const runText = runs != null ? runs.toString() : "-";
  return (
    <span className={`${styles.resultBadge} ${cls}`} title={`${graded} · 1st ${runText}R · ${actual ?? "-"}`}>
      <span className={styles.resultGlyph}>{glyph}</span>
      <span className={styles.resultRuns}>{runText}R</span>
    </span>
  );
}
