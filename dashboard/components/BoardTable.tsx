"use client";

import { useMemo, useState } from "react";
import { selectTopPick } from "@/lib/top-pick-rank";
import type { BoardRow, GameDetail, PickThresholds } from "@/lib/types";
import type { ReplayStake } from "@/lib/season-record";
import { BoardRowItem } from "./BoardRow";
import { useLiveGameState } from "@/lib/useLiveGameState";
import styles from "./BoardTable.module.css";

type SortKey = "default" | "edge" | "result";

export function BoardTable({
  rows,
  details,
  totalCount,
  loading,
  thresholds,
  replayStakes,
  date,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
  totalCount: number;
  loading: boolean;
  thresholds?: PickThresholds;
  replayStakes?: Map<string, ReplayStake>;
  date?: string;
}) {
  // Tier-1 live MLB state: poll /api/live-state every 30s for in-progress
  // games on today's date.  Hook auto-skips for historical dates so we
  // don't waste bandwidth on yesterday's slate.  Returns gamePk + team
  // lookup tables for cheap O(1) row matching.
  const liveState = useLiveGameState(date ?? null, 30_000);
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

  /* THE SLATE'S #1 BET.
   *
   * Operator, 2026-07-30: "i would like to be able to tell which bet is
   * the top bet." The /history card tracks the #1 pick's record, but the
   * board never said which of tonight's plays IS it -- so the number was
   * only ever readable after the fact.
   *
   * Ranked with `compareForTopPick`, the SAME comparator lib/top-pick
   * uses for the historical record. Sharing it is the point: two
   * surfaces answering "which was #1" with their own private rule is
   * precisely how this dashboard has produced contradictions before.
   *
   * STRONG ONLY. LEAN is tracked and never wagered, so a "top bet" that
   * is not a bet would be an instruction to risk money the system does
   * not intend to risk -- the same rule the stake chip enforces.
   *
   * Computed over `rows`, not `sortedRows`: the #1 pick is a property of
   * the slate, so re-sorting the table by edge or result must not change
   * which game holds the badge.
   */
  const topPickKey = useMemo(() => {
    /* The fold itself now lives in lib/top-pick-rank so the board, the
       history card and the brief page cannot drift apart about which
       game is #1. STRONG-only filter stays here: see the note above. */
    const best = selectTopPick(
      rows
        .filter((r) => r.pickStrength === "STRONG")
        .map((r) => {
          const d = lookupDetail(r);
          const oddsStr =
            r.pickSide === "YRFI" ? d?.marketYrfiOdds : d?.marketNrfiOdds;
          const odds = oddsStr ? Number.parseFloat(String(oddsStr)) : NaN;
          return {
            key: r.gamePk || `${r.away}@${r.home}#${r.rank}`,
            name: `${r.away}@${r.home}`,
            side: r.pickSide,
            modelP: r.nrfiP,   // FULL precision -- never nrfiPct (display, 1dp)
            odds: Number.isFinite(odds) && odds !== 0 ? odds : null,
          };
        }),
    );
    return best ? best.key : null;
  }, [rows, details]);

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
      {/* Post-redesign 6-col header.  Mirrors .clickable in
          BoardRow.module.css.  Edge sort lives on the PICK header
          since edge is now folded into the inline OddsChip in that
          column; result sort lives on the STATE header. */}
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
        >
          State {sortKey === "result" ? "▾" : ""}
        </button>
        <button
          type="button"
          onClick={toggleEdgeSort}
          className={`${styles.sortable} ${
            sortKey === "edge" ? styles.sortableActive : ""
          }`}
          aria-pressed={sortKey === "edge"}
          title={sortKey === "edge" ? "Sorted by edge (click to reset)" : "Sort by edge (highest first)"}
        >
          Pick {sortKey === "edge" ? "· edge ▾" : ""}
        </button>
        <div className={styles.distHead}>NRFI ←→ YRFI</div>
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
          // Live game state lookup (T2.28): primary key is gamePk; fall
          // back to team key for legacy rows without gamePk.
          const live =
            (row.gamePk && liveState.byGamePk[row.gamePk]) ||
            liveState.byTeam[`${row.away}@${row.home}`] ||
            undefined;
          return (
            <BoardRowItem
              key={key}
              row={row}
              isTopPick={topPickKey != null && key === topPickKey}
              detail={detail}
              live={live}
              expanded={expanded.has(key)}
              onToggle={() => setExpanded((prev) => {
                const next = new Set(prev);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })}
              thresholds={thresholds}
              replayStakes={replayStakes}
              slateDate={date ?? ""}
            />
          );
        })}
      </div>

      {/* WHAT MAKES IT #1, SAID OUT LOUD (2026-07-31).
          Operator: "what even makes it the #1 pick?" It was only ever
          in the badge's hover tooltip, which is not discoverable and
          does not exist on a phone at all. A marker whose meaning has
          to be hunted for is a marker that gets mistrusted -- and this
          one points at the single number on the history page that never
          rewrites itself, so it is worth spelling out.
          Rendered only when a badge is actually on the board. */}
      {topPickKey && (
        <p className={styles.topPickNote}>
          <span className={styles.topPickNoteBadge}>#1</span>
          The model&rsquo;s most confident bet tonight — the STRONG play
          furthest from a coin flip. When two are equally confident, the
          one at the better price wins.{" "}
          <a href="/brief" className={styles.topPickNoteLink}>
            Read the brief
          </a>{" "}
          for why it is on, what argues against it, and both teams&rsquo;
          recent first innings. Its running record is the{" "}
          <a href="/history" className={styles.topPickNoteLink}>
            #1 pick card
          </a>{" "}
          on the history page.
        </p>
      )}
    </div>
  );
}
