"use client";

import styles from "./LambdaMeter.module.css";

// Pick zones in P(YRFI) probability space.  Mirrors calibration.py /
// classify_pick_calibrated() in the predictor.  Read left-to-right:
//   0.00 -> certain NRFI (no run)
//   0.50 -> coin flip (NRFI/YRFI line)
//   1.00 -> certain YRFI (run scored)
const STRONG_NRFI_MAX = 0.40; // P(YRFI) < 0.40  -> STRONG NRFI
const LEAN_NRFI_MAX   = 0.47; // P(YRFI) < 0.47  -> LEAN NRFI
const PASS_MAX        = 0.53; // P(YRFI) < 0.53  -> PASS
const LEAN_YRFI_MAX   = 0.60; // P(YRFI) < 0.60  -> LEAN YRFI
//                              P(YRFI) >= 0.60  -> STRONG YRFI

function pct(x: number): number {
  return Math.max(0, Math.min(100, x * 100));
}

export function LambdaMeter({
  yrfiProb,
  compact = false,
}: {
  yrfiProb: number; // 0..1
  compact?: boolean;
}) {
  const pos = pct(yrfiProb);

  const zones = [
    { from: 0,                to: STRONG_NRFI_MAX, tone: "strongNrfi" },
    { from: STRONG_NRFI_MAX,  to: LEAN_NRFI_MAX,   tone: "leanNrfi"   },
    { from: LEAN_NRFI_MAX,    to: PASS_MAX,        tone: "pass"       },
    { from: PASS_MAX,         to: LEAN_YRFI_MAX,   tone: "leanYrfi"   },
    { from: LEAN_YRFI_MAX,    to: 1.0,             tone: "strongYrfi" },
  ];

  return (
    <span
      className={`${styles.meter} ${compact ? styles.compact : ""}`}
      aria-label={`P(YRFI) ${yrfiProb.toFixed(3)}`}
      role="img"
    >
      <span className={styles.track}>
        {zones.map((z) => {
          const left = pct(z.from);
          const width = pct(z.to) - left;
          return (
            <span
              key={z.tone}
              className={`${styles.zone} ${styles[z.tone]}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              aria-hidden
            />
          );
        })}
        <span
          className={styles.midMark}
          style={{ left: `${pct(0.50)}%` }}
          aria-hidden
        />
        <span className={styles.pin} style={{ left: `${pos}%` }} aria-hidden>
          <span className={styles.pinStem} />
          <span className={styles.pinDiamond} />
        </span>
      </span>
      {!compact && (
        <span className={styles.scale}>
          <span>NRFI</span>
          <span>0.40</span>
          <span>0.50</span>
          <span>0.60</span>
          <span>YRFI</span>
        </span>
      )}
    </span>
  );
}
