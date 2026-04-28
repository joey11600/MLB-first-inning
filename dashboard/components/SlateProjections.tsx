"use client";

import type { BoardRow, GameDetail } from "@/lib/types";
import styles from "./SlateProjections.module.css";

/**
 * Slate Projections — card-row layout inspired by modern dashboard tables
 * (each row = elevated card, status gradient, CPU-style bars, badge pills).
 *
 * Per-row anatomy:
 *   [Rank]  [Matchup]  [Home pitcher]  [Away pitcher]  [Time]
 *   [Projected runs bars + number]  [STRONG / PASS badge]
 *
 * Each row is its own card with a status-tinted right-edge gradient,
 * staggered fade-in on mount, and a subtle hover lift.
 */

type Zone = "NRFI_STRONG" | "PASS" | "YRFI_STRONG";
type ZoneColor = "green" | "red" | "muted";

export function SlateProjections({
  rows,
  details,
}: {
  rows: BoardRow[];
  details: Record<string, GameDetail>;
}) {
  // Sort by combined lambda ascending (best NRFI -> best YRFI).
  const sorted = [...rows].sort((a, b) => {
    const da = details[a.gamePk || `${a.away}@${a.home}`];
    const db = details[b.gamePk || `${b.away}@${b.home}`];
    const la = da?.lambdaLrTotal ?? 99;
    const lb = db?.lambdaLrTotal ?? 99;
    return la - lb;
  });

  const visible = sorted
    .map((row) => {
      const key = row.gamePk || `${row.away}@${row.home}`;
      const d = details[key];
      if (!d) return null;
      return { row, key, d, zone: rowZone(row) };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);

  const nStrong = visible.filter((v) => v.zone !== "PASS").length;
  const nPass = visible.length - nStrong;

  return (
    <section className={styles.wrap}>
      {/* Header: pulse dot + title + summary count, like the reference */}
      <header className={styles.head}>
        <div className={styles.headLeft}>
          <span className={styles.pulse} aria-hidden="true" />
          <h2 className={styles.title}>Slate Projections</h2>
          <span className={styles.subtitle}>
            {nStrong} strong &middot; {nPass} pass &middot; {visible.length} games
          </span>
        </div>
        <div className={styles.legend}>
          <span className={styles.legendKey} data-tone="nrfi">
            ≤ 0.55 NRFI
          </span>
          <span className={styles.legendKey} data-tone="yrfi">
            ≥ 1.05 YRFI
          </span>
        </div>
      </header>

      {/* Column headers — uppercase tracked-out, muted */}
      <div className={styles.headerRow}>
        <div className={styles.col_rank}>No</div>
        <div className={styles.col_match}>Matchup</div>
        <div className={styles.col_pname}>Home pitcher</div>
        <div className={styles.col_pname}>Away pitcher</div>
        <div className={styles.col_time}>1st Pitch</div>
        <div className={styles.col_runs}>Proj. 1st-inn runs</div>
        <div className={styles.col_zone}>Zone</div>
      </div>

      {/* Rows */}
      <div className={styles.rows}>
        {visible.map((item, i) => {
          const rank = String(i + 1).padStart(2, "0");
          const { row, key, d, zone } = item;
          const total = d.lambdaLrTotal;
          const t1 = d.lambdaLrT1;
          const b1 = d.lambdaLrB1;
          const homeName = d.home?.pitcher?.name?.trim() || "TBD";
          const awayName = d.away?.pitcher?.name?.trim() || "TBD";
          const homeTbd = isTbd(homeName);
          const awayTbd = isTbd(awayName);
          const color = zoneColor(zone);

          return (
            <div
              key={key}
              className={styles.row}
              data-zone={color}
              style={{ animationDelay: `${i * 60}ms` }}
            >
              {/* Soft status gradient on the right edge */}
              <div className={styles.statusGradient} aria-hidden="true" />

              {/* Rank */}
              <div className={styles.col_rank}>
                <span className={styles.rankNum}>{rank}</span>
              </div>

              {/* Matchup with team chips */}
              <div className={styles.col_match}>
                <span className={styles.teamChip}>{row.away}</span>
                <span className={styles.at}>@</span>
                <span className={styles.teamChip} data-home="true">
                  {row.home}
                </span>
                {row.doubleHeader && row.doubleHeader !== "N" && (
                  <span className={styles.dhTag}>DH-{row.gameNumber}</span>
                )}
              </div>

              {/* Home pitcher */}
              <div className={styles.col_pname}>
                <span className={styles.pitcherIcon} aria-hidden="true">
                  H
                </span>
                {homeTbd ? (
                  <span className={styles.tbd}>TBD</span>
                ) : (
                  <span className={styles.pname}>
                    {homeName}
                    <span className={styles.pmeta}>{fmt(t1)} top 1st</span>
                  </span>
                )}
              </div>

              {/* Away pitcher */}
              <div className={styles.col_pname}>
                <span className={styles.pitcherIcon} data-away="true" aria-hidden="true">
                  A
                </span>
                {awayTbd ? (
                  <span className={styles.tbd}>TBD</span>
                ) : (
                  <span className={styles.pname}>
                    {awayName}
                    <span className={styles.pmeta}>{fmt(b1)} bot 1st</span>
                  </span>
                )}
              </div>

              {/* Game time */}
              <div className={styles.col_time}>
                <span className={styles.timeText}>{row.gameTimeEt || "—"}</span>
              </div>

              {/* Projected runs visualization (bars + number, CPU-style) */}
              <div className={styles.col_runs}>
                <RunBars total={total} color={color} />
              </div>

              {/* Zone badge */}
              <div className={styles.col_zone}>
                <ZoneBadge zone={zone} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ---------- sub-components ---------- */

/** 10-bar visualization of projected first-inning runs.
 *  Each bar = 0.1 expected runs.  10 bars = 1.0 (the YRFI threshold area). */
function RunBars({
  total,
  color,
}: {
  total: number | null;
  color: ZoneColor;
}) {
  const value = total ?? 0;
  // Scale: 0.0 -> 0 bars, 1.0 -> 10 bars. Cap at 12 for the over-1.0 case.
  const filled = Math.max(0, Math.min(10, Math.round(value * 10)));

  return (
    <div className={styles.bars}>
      <div className={styles.barTrack}>
        {Array.from({ length: 10 }).map((_, idx) => (
          <span
            key={idx}
            className={styles.bar}
            data-filled={idx < filled ? "true" : "false"}
            data-tone={color}
          />
        ))}
      </div>
      <span className={styles.barNum}>{fmt(total)}</span>
    </div>
  );
}

function ZoneBadge({ zone }: { zone: Zone }) {
  if (zone === "NRFI_STRONG") {
    return (
      <span className={styles.badge} data-tone="green">
        STRONG NRFI
      </span>
    );
  }
  if (zone === "YRFI_STRONG") {
    return (
      <span className={styles.badge} data-tone="red">
        STRONG YRFI
      </span>
    );
  }
  return (
    <span className={styles.badge} data-tone="muted">
      Pass
    </span>
  );
}

/* ---------- helpers ---------- */

function fmt(n: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

function isTbd(name: string): boolean {
  return !name || name.toUpperCase() === "TBD";
}

function rowZone(row: BoardRow): Zone {
  if (row.pickSide === "NRFI" && row.pickStrength === "STRONG") return "NRFI_STRONG";
  if (row.pickSide === "YRFI" && row.pickStrength === "STRONG") return "YRFI_STRONG";
  return "PASS";
}

function zoneColor(zone: Zone): ZoneColor {
  if (zone === "NRFI_STRONG") return "green";
  if (zone === "YRFI_STRONG") return "red";
  return "muted";
}
