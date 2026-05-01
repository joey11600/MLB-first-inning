"use client";

import { useMemo, useState } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import { BoardRowItem } from "./BoardRow";
import styles from "./BoardTable.module.css";

type SortKey = "default" | "edge";

export function BoardTable({
  rows,
  details,
  totalCount,
  loading,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  totalCount: number;
  loading: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  // Sort state: "default" = original lambda-ranked order; "edge" = highest
  // edge first (puts the most-betable plays at the top regardless of game time).
  const [sortKey, setSortKey] = useState<SortKey>("default");

  // Memoized sort: avoid recomputing on every keystroke / unrelated re-render.
  const sortedRows = useMemo(() => {
    if (sortKey !== "edge") return rows;
    // Sort descending by edge.  Rows without odds (or PASS picks) sink to bottom.
    return [...rows].sort((a, b) => {
      const aKey = a.gamePk || `${a.away}@${a.home}`;
      const bKey = b.gamePk || `${b.away}@${b.home}`;
      const aEdge = details[aKey]?.edgeOnPick;
      const bEdge = details[bKey]?.edgeOnPick;
      const aVal = aEdge != null && a.pickSide !== "PASS" ? aEdge : -Infinity;
      const bVal = bEdge != null && b.pickSide !== "PASS" ? bEdge : -Infinity;
      return bVal - aVal;
    });
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

  const toggleEdgeSort = () => setSortKey((k) => (k === "edge" ? "default" : "edge"));

  return (
    <div className={styles.wrap}>
      <div className={styles.headRow} role="row">
        <div>Time</div>
        <div>Matchup</div>
        <div>Result</div>
        <div className={styles.right}>P(YRFI)</div>
        <div>Pick</div>
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
              expanded={expanded === key}
              onToggle={() => setExpanded(expanded === key ? null : key)}
            />
          );
        })}
      </div>
    </div>
  );
}
