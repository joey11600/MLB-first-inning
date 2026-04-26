"use client";

import { useEffect, useRef, useState } from "react";
import type { BoardRow } from "@/lib/types";
import styles from "./Ticker.module.css";

const SCROLL_PX_PER_SEC = 60; // ticker speed

export function Ticker({ rows, date }: { rows: BoardRow[]; date: string }) {
  const avgLambda =
    rows.length === 0
      ? 0
      : rows.reduce((a, r) => a + r.lambda, 0) / rows.length;

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [paused, setPaused] = useState(false);

  // RAF-driven scroll. The inner element contains the segments duplicated,
  // so we just translate -X until we've moved one full segment-width, then
  // reset to 0 for a seamless loop.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    let rafId = 0;
    let lastTs = 0;
    let offset = 0;

    // Honor reduced-motion preference.
    const prefersReduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (prefersReduced) return;

    const tick = (ts: number) => {
      if (lastTs === 0) lastTs = ts;
      const dt = (ts - lastTs) / 1000;
      lastTs = ts;

      if (!paused) {
        offset += SCROLL_PX_PER_SEC * dt;
        // The inner content is rendered twice; loop when we've advanced past
        // half its width so the seam is invisible.
        const half = el.scrollWidth / 2;
        if (half > 0 && offset >= half) offset -= half;
        el.style.transform = `translate3d(${-offset}px, 0, 0)`;
      }

      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [paused, rows.length]);

  const segments = rows.map((r) => {
    const cls =
      r.pickSide === "NRFI"
        ? styles.nrfi
        : r.pickSide === "YRFI"
          ? styles.yrfi
          : styles.pass;
    return (
      <span key={`${r.rank}-${r.away}-${r.home}`} className={`${styles.seg} ${cls}`}>
        <span className={styles.matchup}>
          {r.away} @ {r.home}
        </span>
        <span className={styles.lambda}>λ {r.lambda.toFixed(3)}</span>
        <span className={styles.pct}>
          {r.pickSide === "YRFI"
            ? `Y ${r.yrfiPct.toFixed(1)}%`
            : `N ${r.nrfiPct.toFixed(1)}%`}
        </span>
      </span>
    );
  });

  return (
    <div className={styles.bar} role="marquee" aria-label="Slate ticker">
      <div className={styles.label}>
        <span className={styles.dot} />
        Live slate · <span className={styles.lambda}>λ̄ {avgLambda.toFixed(3)}</span> · {rows.length} games
      </div>
      <div
        className={styles.track}
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        <div className={styles.scroll} ref={scrollRef}>
          {segments}
          {segments}
        </div>
      </div>
      <div className={styles.rightCap}>
        {date ? date.replace(/-/g, ".") : "—"}
      </div>
    </div>
  );
}
