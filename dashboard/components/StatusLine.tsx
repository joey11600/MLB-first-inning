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
  const parts = [
    { k: "SRC", v: "board.csv", tone: "accent" },
    { k: "DATE", v: date || "—" },
    { k: "ROWS", v: `${count}/${total}` },
    { k: "SIDE", v: filters.side },
    { k: "STR", v: filters.strength },
    { k: "SORT", v: filters.sort.toUpperCase() },
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
            <span className={`${styles.v} ${p.tone === "accent" ? styles.vAccent : ""}`}>{p.v}</span>
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
