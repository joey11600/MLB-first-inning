"use client";

import { useState } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import { BoardRowItem } from "./BoardRow";
import styles from "./BoardTable.module.css";

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

  return (
    <div className={styles.wrap}>
      <div className={styles.headRow} role="row">
        <div>Time</div>
        <div>Matchup</div>
        <div>Result</div>
        <div className={styles.right}>P(YRFI)</div>
        <div>Pick</div>
        <div>NRFI ←→ YRFI</div>
        <div className={styles.right}>NRFI</div>
        <div className={styles.right}>YRFI</div>
        <div></div>
      </div>
      <div
        className={`${styles.body} stagger ${loading ? styles.dim : ""}`}
      >
        {rows.map((row) => {
          // gamePk is the unique row key (handles doubleheaders correctly).
          // Fall back to away@home#rank for old board CSVs missing gamePk.
          const key       = row.gamePk || `${row.away}@${row.home}#${row.rank}`;
          const detailKey = row.gamePk || `${row.away}@${row.home}`;
          const detail    = details[detailKey];
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
