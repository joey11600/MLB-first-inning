"use client";

import type { Filters } from "./ControlPanel";
import styles from "./StatusLine.module.css";

export function StatusLine({
  count,
  total,
  filters,
  date,
}: {
  count: number;
  total: number;
  filters: Filters;
  date: string;
}) {
  // Pretty-print the strength filter for the status bar.
  // LEAN+ is "STRONG + LEAN" (Phase 1.3 reactivated LEAN tier as
  // track-only on 2026-05-12 -- bets only fire on STRONG, but LEAN
  // is logged for break-even analysis).
  const strLabel =
    filters.strength === "LEAN+"  ? "STRONG+LEAN" :
    filters.strength === "STRONG" ? "STRONG" : "ALL";

  const parts = [
    { k: "DATE",     v: date || "—" },
    { k: "GAMES",    v: `${count}/${total}` },
    { k: "SIDE",     v: filters.side },
    { k: "STRENGTH", v: strLabel },
    { k: "SORT",     v: filters.sort.replace("-", " ").toUpperCase() },
  ];

  return (
    <footer className={styles.bar}>
      <div className={styles.left}>
        <span className={styles.glyph}>⬥</span>
        <span className={styles.brand}>NRFI-TERM</span>
      </div>
      <div className={styles.cells}>
        {parts.map((p) => (
          <span key={p.k} className={styles.cell}>
            <span className={styles.k}>{p.k}</span>
            <span className={styles.v}>{p.v}</span>
          </span>
        ))}
      </div>
      <div className={styles.right}>
        <span className={styles.legend}>
          <i className={styles.sw} data-t="nrfi" />NRFI
        </span>
        <span className={styles.legend}>
          <i className={styles.sw} data-t="pass" />PASS
        </span>
        <span className={styles.legend}>
          <i className={styles.sw} data-t="yrfi" />YRFI
        </span>
      </div>
    </footer>
  );
}
