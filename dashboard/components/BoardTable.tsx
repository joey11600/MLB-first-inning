"use client";

import { useMemo, useState } from "react";
import type { BoardRow, GameDetail, PickThresholds } from "@/lib/types";
import { BoardRowItem } from "./BoardRow";
import styles from "./BoardTable.module.css";

type SortKey = "default" | "edge" | "result";

export function BoardTable({
  rows,
  details,
  totalCount,
  loading,
  thresholds,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  totalCount: number;
  loading: boolean;
  thresholds?: PickThresholds;
}) {
  // T4.24: multi-row expand so the user can pin 2+ games open and
  // compare their feature breakdowns side-by-side.  Click a row to
  // toggle its expansion; previously open rows stay open.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  // Sort state:
  //   "default" = lambda-ranked, the predictor's original ordering
  //   "edge"    = highest edge first (most-betable plays at top)
  //   "result"  = group by graded outcome (W → L → PASS → PP → ungraded),
  //               useful for backreading the slate after games finish
  const [sortKey, setSortKey] = useState<SortKey>("default");

  // Helper: row → detail using the same fallback chain as the render.
  function lookupDetail(r: BoardRow) {
    return (
      (r.gamePk && details[r.gamePk]) ||
      details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
      details[`${r.away}@${r.home}`]
    );
  }

  // Memoized sort: avoid recomputing on every keystroke / unrelated re-render.
  const sortedRows = useMemo(() => {
    if (sortKey === "default") return rows;
    if (sortKey === "edge") {
      // Sort descending by edge.  Rows without odds (or PASS picks) sink to bottom.
      return [...rows].sort((a, b) => {
        const aEdge = lookupDetail(a)?.edgeOnPick;
        const bEdge = lookupDetail(b)?.edgeOnPick;
        const aVal = aEdge != null && a.pickSide !== "PASS" ? aEdge : -Infinity;
        const bVal = bEdge != null && b.pickSide !== "PASS" ? bEdge : -Infinity;
        return bVal - aVal;
      });
    }
    // "result" — group graded outcomes together so the user can scan
    // wins/losses for the slate as a unit.  Order:
    //   1. WIN  (most satisfying, top)
    //   2. LOSS
    //   3. PASS (graded but no money risked)
    //   4. POSTPONED / SUSPENDED
    //   5. ungraded (still upcoming or in progress)
    // Within each bucket, fall back to the original (lambda-ranked) order.
    const bucket = (r: BoardRow): number => {
      const g = (lookupDetail(r)?.gradedResult ?? "") as string;
      if (g === "WIN")       return 0;
      if (g === "LOSS")      return 1;
      if (g === "PASS")      return 2;
      if (g === "POSTPONED" || g === "SUSPENDED") return 3;
      return 4;
    };
    return [...rows]
      .map((r, idx) => ({ r, idx }))
      .sort((a, b) => {
        const ba = bucket(a.r), bb = bucket(b.r);
        if (ba !== bb) return ba - bb;
        return a.idx - b.idx;  // stable within bucket
      })
      .map(x => x.r);
  }, [rows, sortKey, details]);

  if (rows.length === 0) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyFig} aria-hidden>—</div>
        <div className={styles.emptyTitle}>No games match</div>
        <div className={styles.emptySub}>
          {totalCount === 0
            ? "The board for this date is empty or the CSV is missing."
            : "Adjust the filters to see more picks."}
        </div>
      </div>
    );
  }

  const toggleEdgeSort   = () => setSortKey((k) => (k === "edge"   ? "default" : "edge"));
  const toggleResultSort = () => setSortKey((k) => (k === "result" ? "default" : "result"));

  return (
    <div className={styles.wrap}>
      <div className={styles.headRow} role="row">
        <div>Time</div>
        <div>Matchup</div>
        <button
          type="button"
          onClick={toggleResultSort}
          className={`${styles.sortable} ${
            sortKey === "result" ? styles.sortableActive : ""
          }`}
          aria-pressed={sortKey === "result"}
          title={sortKey === "result" ? "Sorted by result (click to reset)" : "Sort by result (W → L → PASS → PP → ungraded)"}
          style={{ textAlign: "left" }}
        >
          Result {sortKey === "result" ? "▾" : ""}
        </button>
        <div className={styles.right}>P(YRFI)</div>
        <div>Pick</div>
        <div>Odds</div>
        <button
          type="button"
          onClick={toggleEdgeSort}
          className={`${styles.sortable} ${styles.right} ${
            sortKey === "edge" ? styles.sortableActive : ""
          }`}
          aria-pressed={sortKey === "edge"}
          title={sortKey === "edge" ? "Sorted by edge (click to reset)" : "Sort by edge (highest first)"}
        >
          Edge {sortKey === "edge" ? "▾" : ""}
        </button>
        <div>NRFI ←→ YRFI</div>
        <div className={styles.right}>NRFI</div>
        <div className={styles.right}>YRFI</div>
        <div></div>
      </div>
      <div
        className={`${styles.body} stagger ${loading ? styles.dim : ""}`}
      >
        {sortedRows.map((row) => {
          const key = row.gamePk || `${row.away}@${row.home}#${row.rank}`;
          // Detail-key fallback chain: gamePk (canonical) -> away@home#N
          // (DH-aware) -> away@home (single-game-only legacy fallback).
          // The middle key matches loadDetails()' DH-aware insertion so
          // DH-2 rows from older board CSVs without gamePk don't collide
          // with DH-1's detail and render the wrong pitcher / lineup.
          const detail =
            (row.gamePk && details[row.gamePk]) ||
            details[`${row.away}@${row.home}#${row.gameNumber || 1}`] ||
            details[`${row.away}@${row.home}`];
          return (
            <BoardRowItem
              key={key}
              row={row}
              detail={detail}
              expanded={expanded.has(key)}
              onToggle={() => setExpanded((prev) => {
                const next = new Set(prev);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })}
              thresholds={thresholds}
            />
          );
        })}
      </div>
    </div>
  );
}
