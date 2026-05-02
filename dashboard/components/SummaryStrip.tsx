"use client";

import type { BoardRow, GameDetail } from "@/lib/types";
import styles from "./SummaryStrip.module.css";

interface Bucket {
  key: string;
  label: string;
  className: string;
  test: (r: BoardRow) => boolean;
}

// Only three zones now -- LEAN tier was eliminated when we collapsed
// thresholds (LEAN_*_P = STRONG_*_P) per the user's "STRONG plays only"
// direction.  Keep the bucket array short so the distribution UI shows
// 3 meaningful slices instead of 5 with two always-zero columns.
const BUCKETS: Bucket[] = [
  {
    key: "strong-nrfi",
    label: "Strong NRFI",
    className: "strongNrfi",
    test: (r) => r.pickSide === "NRFI" && r.pickStrength === "STRONG",
  },
  {
    key: "pass",
    label: "Pass",
    className: "pass",
    test: (r) =>
      r.pickSide === "PASS" ||
      (r.pickStrength !== "STRONG" && r.pickStrength !== "LEAN"),
  },
  {
    key: "strong-yrfi",
    label: "Strong YRFI",
    className: "strongYrfi",
    test: (r) => r.pickSide === "YRFI" && r.pickStrength === "STRONG",
  },
];

export function SummaryStrip({
  rows,
  details = {},
}: {
  rows: BoardRow[];
  details?: Record<string, GameDetail>;
}) {
  const avgLambda =
    rows.length === 0 ? 0 : rows.reduce((a, r) => a + r.lambda, 0) / rows.length;
  const maxLambda =
    rows.length === 0 ? 0 : Math.max(...rows.map((r) => r.lambda));
  const minLambda =
    rows.length === 0 ? 0 : Math.min(...rows.map((r) => r.lambda));
  const nrfiCnt = rows.filter((r) => r.pickSide === "NRFI").length;
  const yrfiCnt = rows.filter((r) => r.pickSide === "YRFI").length;

  // T1.4 Today's running P&L tile.  Computed from the details map so it
  // updates live as polling fetches fresh data.  Only counts rows with
  // graded WIN/LOSS results (PASS picks contribute 0 to record, blank
  // graded means in-progress).  Uses profit_loss_units when populated
  // (real bet at real price after T2.27), else flat -110 fallback.
  function lookupDetail(r: BoardRow): GameDetail | undefined {
    return (
      (r.gamePk && details[r.gamePk]) ||
      details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
      details[`${r.away}@${r.home}`]
    );
  }
  const DEFAULT_WIN = 100 / 110;       // 0.909
  const DEFAULT_LOSS = -1.0;
  let strongPicks = 0;
  let bets = 0;     // graded WIN/LOSS (any side)
  let wins = 0;
  let losses = 0;
  let pl = 0;
  for (const r of rows) {
    if (r.pickStrength === "STRONG" && (r.pickSide === "NRFI" || r.pickSide === "YRFI")) {
      strongPicks += 1;
    }
    const d = lookupDetail(r);
    const g = d?.gradedResult;
    if (g === "WIN" || g === "LOSS") {
      bets += 1;
      if (g === "WIN") wins += 1;
      else losses += 1;
      const realPl = d?.profitLossUnits;
      if (typeof realPl === "number" && Number.isFinite(realPl)) {
        pl += realPl;
      } else {
        pl += g === "WIN" ? DEFAULT_WIN : DEFAULT_LOSS;
      }
    }
  }
  const ungraded = strongPicks - bets;   // STRONG bets still pending
  const plSign = pl >= 0 ? "+" : "";
  const plClass = pl > 0 ? styles.plPositive : pl < 0 ? styles.plNegative : "";
  const winRate = bets > 0 ? (wins / bets) * 100 : null;

  return (
    <section className={styles.wrap} aria-label="Slate summary">
      <div className={styles.tiles}>
        <div className={`${styles.tile} ${styles.primary}`}>
          <div className="eyebrow">Games today</div>
          <div className={styles.big}>{rows.length}</div>
          <div className={styles.foot}>
            <span className={styles.footCell}>
              <span className={styles.tinyDot} data-tone="nrfi" />NRFI {nrfiCnt}
            </span>
            <span className={styles.footCell}>
              <span className={styles.tinyDot} data-tone="yrfi" />YRFI {yrfiCnt}
            </span>
          </div>
        </div>

        <div className={styles.tile}>
          <div className="eyebrow">Avg λ</div>
          <div className={styles.big}>{avgLambda.toFixed(3)}</div>
          <div className={styles.foot}>
            <span className="num">{minLambda.toFixed(3)}</span>
            <span className={styles.sep}>—</span>
            <span className="num">{maxLambda.toFixed(3)}</span>
          </div>
        </div>

        <div className={`${styles.tile} ${styles.plTile}`}>
          <div className="eyebrow">Today P&amp;L</div>
          <div className={`${styles.big} ${plClass}`}>
            {plSign}{pl.toFixed(2)}
            <span className={styles.plUnit}>u</span>
          </div>
          <div className={styles.foot}>
            {bets > 0 ? (
              <>
                <span className={styles.footCell}>
                  <span className={styles.tinyDot} data-tone="nrfi" />
                  {wins}-{losses}
                </span>
                {winRate !== null && (
                  <span className={styles.footCell}>
                    <span className="num">{winRate.toFixed(0)}%</span>
                  </span>
                )}
                {ungraded > 0 && (
                  <span className={styles.footCell}>
                    <span className={styles.pendingDot} aria-hidden />
                    {ungraded} pending
                  </span>
                )}
              </>
            ) : ungraded > 0 ? (
              <span className={styles.footCell}>
                <span className={styles.pendingDot} aria-hidden />
                {ungraded} STRONG pending
              </span>
            ) : (
              <span className={styles.footCell}>no bets graded yet</span>
            )}
          </div>
        </div>

        <div className={styles.distribution}>
          <div className="eyebrow">Distribution</div>
          <div className={styles.buckets}>
            {BUCKETS.map((b) => {
              const count = rows.filter(b.test).length;
              const pct = rows.length === 0 ? 0 : (count / rows.length) * 100;
              return (
                <div
                  key={b.key}
                  className={`${styles.bucket} ${styles[b.className]}`}
                >
                  <div className={styles.bucketHead}>
                    <span className={styles.bucketLabel}>{b.label}</span>
                    <span className={`num ${styles.bucketCount}`}>
                      {count}
                    </span>
                  </div>
                  <div className={styles.bucketBar}>
                    <div
                      className={styles.bucketFill}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className={`num ${styles.bucketPct}`}>
                    {pct.toFixed(0)}%
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
