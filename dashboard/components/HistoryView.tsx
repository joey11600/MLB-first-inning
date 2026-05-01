"use client";

import { useMemo, useState } from "react";
import type { RoiResponse, RoiWindow } from "@/lib/roi";
import styles from "./HistoryView.module.css";

const WINDOWS: { key: RoiWindow; label: string }[] = [
  { key: "7d",     label: "Last 7 days" },
  { key: "30d",    label: "Last 30 days" },
  { key: "season", label: "Season" },
];

interface DayRecord {
  date: string;
  units: number;       // daily P&L
  cumulative: number;  // running total
}

export function HistoryView({ initial }: { initial: RoiResponse }) {
  const [data, setData]     = useState<RoiResponse>(initial);
  const [window, setWindow] = useState<RoiWindow>(initial.window);
  const [loading, setLoading] = useState(false);

  async function changeWindow(w: RoiWindow) {
    if (w === window) return;
    setWindow(w);
    setLoading(true);
    try {
      const res = await fetch(`/api/roi?window=${w}`, { cache: "no-store" });
      if (res.ok) setData((await res.json()) as RoiResponse);
    } finally {
      setLoading(false);
    }
  }

  // Derive per-day records from cumulativePL.  Daily = cum[i] - cum[i-1].
  const days = useMemo<DayRecord[]>(() => {
    const out: DayRecord[] = [];
    let prev = 0;
    for (const row of data.cumulativePL) {
      out.push({
        date:       row.date,
        units:      row.units - prev,
        cumulative: row.units,
      });
      prev = row.units;
    }
    return out;
  }, [data.cumulativePL]);

  const totalUnits = days.length ? days[days.length - 1].cumulative : 0;
  const totalDays  = days.length;
  const winDays    = days.filter((d) => d.units > 0).length;
  const lossDays   = days.filter((d) => d.units < 0).length;
  const flatDays   = days.filter((d) => d.units === 0).length;
  const bestDay    = days.length ? Math.max(...days.map((d) => d.units)) : 0;
  const worstDay   = days.length ? Math.min(...days.map((d) => d.units)) : 0;

  // Reverse-chronological for the table
  const tableRows = [...days].reverse();

  return (
    <main className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.headLeft}>
          <a href="/" className={styles.backLink} aria-label="Back to slate board">
            <span aria-hidden>◂</span> Slate
          </a>
          <div>
            <div className={styles.eyebrow}>Performance · daily breakdown</div>
            <h1 className={styles.title}>Bankroll history</h1>
          </div>
        </div>
        <div className={styles.windowToggle} role="tablist" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.key}
              role="tab"
              aria-selected={window === w.key}
              onClick={() => changeWindow(w.key)}
              className={`${styles.windowBtn} ${
                window === w.key ? styles.windowBtnActive : ""
              }`}
              type="button"
              disabled={loading}
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      {/* Summary tiles */}
      <section className={styles.tiles}>
        <div className={`${styles.tile} ${tileTone(totalUnits)}`}>
          <div className={styles.tileLabel}>Net units</div>
          <div className={styles.tileBig}>{formatUnits(totalUnits)}</div>
          <div className={styles.tileSub}>across {totalDays} {totalDays === 1 ? "day" : "days"}</div>
        </div>
        <div className={styles.tile}>
          <div className={styles.tileLabel}>Day record</div>
          <div className={styles.tileBig}>
            <span className={styles.numWin}>{winDays}</span>
            <span className={styles.numSep}>/</span>
            <span className={styles.numLoss}>{lossDays}</span>
            {flatDays > 0 && (
              <>
                <span className={styles.numSep}>/</span>
                <span className={styles.numFlat}>{flatDays}</span>
              </>
            )}
          </div>
          <div className={styles.tileSub}>
            up · down{flatDays > 0 ? " · flat" : ""}
          </div>
        </div>
        <div className={styles.tile}>
          <div className={styles.tileLabel}>Best day</div>
          <div className={`${styles.tileBig} ${styles.numWin}`}>
            {formatUnits(bestDay)}
          </div>
          <div className={styles.tileSub}>single-session high</div>
        </div>
        <div className={styles.tile}>
          <div className={styles.tileLabel}>Worst day</div>
          <div className={`${styles.tileBig} ${styles.numLoss}`}>
            {formatUnits(worstDay)}
          </div>
          <div className={styles.tileSub}>single-session low</div>
        </div>
      </section>

      {/* Chart */}
      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <div className={styles.eyebrow}>Equity curve · daily</div>
            <div className={styles.chartTitle}>
              Cumulative P&L · daily +/− at -110
            </div>
          </div>
          <div className={styles.legend}>
            <span className={styles.legendItem}>
              <span className={styles.legendDot} data-tone="win" /> Up day
            </span>
            <span className={styles.legendItem}>
              <span className={styles.legendDot} data-tone="loss" /> Down day
            </span>
            <span className={styles.legendItem}>
              <span className={styles.legendLine} /> Cumulative
            </span>
          </div>
        </div>
        <PnlChart days={days} />
      </section>

      {/* T4.16: Calendar heatmap */}
      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <div className={styles.eyebrow}>Calendar heatmap</div>
            <div className={styles.chartTitle}>Daily P&L by date · green = up, red = down</div>
          </div>
        </div>
        <CalendarHeatmap days={days} />
      </section>

      {/* T4.17: Win-rate by zone */}
      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <div className={styles.eyebrow}>Hit rate by pick zone</div>
            <div className={styles.chartTitle}>
              Wins / bets per zone vs the {(52.4).toFixed(1)}% break-even at -110
            </div>
          </div>
        </div>
        <ZoneHitRateChart zones={data.betZones} />
      </section>

      {/* T4.23: Calibration plot */}
      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <div className={styles.eyebrow}>Calibration check</div>
            <div className={styles.chartTitle}>
              Per-zone predicted probability vs actual hit rate · diagonal = perfect calibration
            </div>
          </div>
        </div>
        <CalibrationPlot zones={data.betZones} />
      </section>

      {/* Table */}
      <section className={styles.tableCard}>
        <div className={styles.eyebrow}>Daily ledger</div>
        <div className={styles.tableWrap}>
          <div className={styles.theadRow}>
            <div>Date</div>
            <div className={styles.right}>Day P&L</div>
            <div className={styles.right}>Cumulative</div>
            <div className={styles.barCell}>Distribution</div>
          </div>
          {tableRows.length === 0 ? (
            <div className={styles.empty}>
              No graded bets in this window yet.
            </div>
          ) : (
            tableRows.map((d) => (
              <DayRow
                key={d.date}
                day={d}
                maxAbs={Math.max(Math.abs(bestDay), Math.abs(worstDay), 0.01)}
              />
            ))
          )}
        </div>
      </section>
    </main>
  );
}

/* ------------- chart ------------- */

function PnlChart({ days }: { days: DayRecord[] }) {
  if (days.length === 0) {
    return <div className={styles.chartEmpty}>No graded days in this window.</div>;
  }

  // Layout
  const W = 1100;
  const H = 280;
  const padL = 56, padR = 12, padT = 16, padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const cumMax = Math.max(0, ...days.map((d) => d.cumulative));
  const cumMin = Math.min(0, ...days.map((d) => d.cumulative));
  const cumRange = cumMax - cumMin || 1;

  const barAbsMax = Math.max(0.01, ...days.map((d) => Math.abs(d.units)));

  // X scale: index based, equal spacing
  const stepX = innerW / Math.max(days.length, 1);
  const xFor = (i: number) => padL + (i + 0.5) * stepX;

  // Y for cumulative line
  const yCum = (v: number) =>
    padT + innerH - ((v - cumMin) / cumRange) * innerH;

  // Y zero baseline for bars (centered)
  const yZero = padT + innerH / 2;
  // Bar height scaled to barAbsMax
  const barH = (v: number) => (Math.abs(v) / barAbsMax) * (innerH / 2 - 4);

  // Build cumulative path
  const linePath = days
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xFor(i).toFixed(1)} ${yCum(d.cumulative).toFixed(1)}`)
    .join(" ");

  // Y axis ticks for cumulative scale
  const tickCount = 5;
  const ticks = Array.from({ length: tickCount }).map((_, i) => {
    const t = cumMin + (cumRange * i) / (tickCount - 1);
    return { v: t, y: yCum(t) };
  });

  // X tick spacing -- show every Nth label so it doesn't overlap
  const labelEvery = Math.max(1, Math.ceil(days.length / 10));

  return (
    <div className={styles.chartScroll}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.chartSvg}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Daily and cumulative P&L chart"
      >
        {/* Grid + Y axis */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={W - padR}
              y1={t.y}
              y2={t.y}
              className={styles.gridLine}
            />
            <text
              x={padL - 8}
              y={t.y + 3}
              textAnchor="end"
              className={styles.axisText}
            >
              {t.v >= 0 ? "+" : ""}{t.v.toFixed(0)}u
            </text>
          </g>
        ))}

        {/* Zero baseline for bars (centered) */}
        <line
          x1={padL}
          x2={W - padR}
          y1={yZero}
          y2={yZero}
          className={styles.gridZero}
        />

        {/* Daily bars */}
        {days.map((d, i) => {
          const x = xFor(i);
          const w = Math.max(2, stepX * 0.55);
          const h = barH(d.units);
          const y = d.units >= 0 ? yZero - h : yZero;
          if (d.units === 0) return null;
          // Tooltip via data-* attributes (avoid SVG <title> hydration mismatch)
          return (
            <rect
              key={i}
              x={x - w / 2}
              y={y}
              width={w}
              height={Math.max(h, 1)}
              className={d.units >= 0 ? styles.barWin : styles.barLoss}
              rx={1}
              data-date={d.date}
              data-units={d.units.toFixed(2)}
            />
          );
        })}

        {/* Cumulative line */}
        <path d={linePath} className={styles.cumLine} />
        {days.map((d, i) => (
          <circle
            key={i}
            cx={xFor(i)}
            cy={yCum(d.cumulative)}
            r={2.5}
            className={styles.cumDot}
            data-date={d.date}
            data-cumulative={d.cumulative.toFixed(2)}
          />
        ))}

        {/* X-axis date labels */}
        {days.map((d, i) =>
          i % labelEvery === 0 ? (
            <text
              key={i}
              x={xFor(i)}
              y={H - padB + 18}
              textAnchor="middle"
              className={styles.axisText}
            >
              {d.date.slice(5)}
            </text>
          ) : null,
        )}
      </svg>
    </div>
  );
}

/* ------------- T4.16 calendar heatmap ------------- */

function CalendarHeatmap({ days }: { days: DayRecord[] }) {
  if (days.length === 0) {
    return <div className={styles.chartEmpty}>No graded days in this window.</div>;
  }
  // Build a date → DayRecord map for O(1) lookup.
  const byDate = new Map(days.map((d) => [d.date, d]));
  // Render a continuous grid from the first to the last day of the window,
  // grouped by week (Sun–Sat).  Pad the start so the first week aligns
  // with its weekday column.
  const first = parseIso(days[0].date)!;
  const last = parseIso(days[days.length - 1].date)!;
  // Walk Sun → Sat starting from the Sunday on/before `first`
  const start = new Date(first);
  start.setUTCDate(start.getUTCDate() - start.getUTCDay());
  // End on the Saturday on/after `last`
  const end = new Date(last);
  end.setUTCDate(end.getUTCDate() + (6 - end.getUTCDay()));
  // Find max abs P&L for color scaling
  const maxAbs = Math.max(0.01, ...days.map((d) => Math.abs(d.units)));
  const cells: { date: string; rec?: DayRecord; inWindow: boolean }[] = [];
  for (
    let cur = new Date(start);
    cur.getTime() <= end.getTime();
    cur.setUTCDate(cur.getUTCDate() + 1)
  ) {
    const iso = cur.toISOString().slice(0, 10);
    const inWindow = cur >= first && cur <= last;
    cells.push({ date: iso, rec: byDate.get(iso), inWindow });
  }
  // Group into 7-cell weeks (rows)
  const weeks: typeof cells[] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return (
    <div className={styles.heatmapWrap}>
      <div className={styles.heatmapDayLabels}>
        {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
          <span key={i}>{d}</span>
        ))}
      </div>
      <div className={styles.heatmapGrid}>
        {weeks.map((wk, wi) => (
          <div key={wi} className={styles.heatmapRow}>
            {wk.map((c, di) => (
              <HeatCell key={c.date} cell={c} maxAbs={maxAbs} di={di} />
            ))}
          </div>
        ))}
      </div>
      <div className={styles.heatmapLegend}>
        <span>Loss</span>
        <span className={styles.heatLegendBar} aria-hidden />
        <span>Win</span>
      </div>
    </div>
  );
}

function HeatCell({
  cell,
  maxAbs,
}: {
  cell: { date: string; rec?: DayRecord; inWindow: boolean };
  maxAbs: number;
  di: number;
}) {
  const rec = cell.rec;
  let bg = "transparent";
  let title = `${cell.date} · no data`;
  if (rec) {
    const intensity = Math.min(1, Math.abs(rec.units) / maxAbs);
    if (rec.units > 0) {
      // primary (warm brown / win)
      bg = `color-mix(in oklab, var(--primary) ${Math.round(15 + intensity * 65)}%, transparent)`;
    } else if (rec.units < 0) {
      bg = `color-mix(in oklab, var(--destructive) ${Math.round(15 + intensity * 65)}%, transparent)`;
    } else {
      bg = `color-mix(in oklab, var(--muted-foreground) 18%, transparent)`;
    }
    title = `${cell.date} · ${formatUnits(rec.units)}`;
  }
  return (
    <span
      className={styles.heatCell}
      title={title}
      style={{
        background: bg,
        opacity: cell.inWindow ? 1 : 0.25,
      }}
      aria-label={title}
    />
  );
}

function parseIso(iso: string): Date | null {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(Date.UTC(y, m - 1, d));
}

/* ------------- T4.17 win-rate by zone ------------- */

function ZoneHitRateChart({ zones }: { zones: import("@/lib/roi").ZoneRoi[] }) {
  const withBets = zones.filter((z) => z.bets > 0);
  if (withBets.length === 0) {
    return <div className={styles.chartEmpty}>No graded bets in this window yet.</div>;
  }
  const breakEven = 0.524; // -110 break-even
  return (
    <div className={styles.zoneChart}>
      {withBets.map((z) => {
        const pct = z.hitRate * 100;
        const above = z.hitRate >= breakEven;
        const fillW = Math.min(100, pct);
        return (
          <div key={z.label} className={styles.zoneRow}>
            <div className={styles.zoneLabel}>
              <span className={styles.zoneName}>{z.label}</span>
              <span className={styles.zoneN}>{z.bets} bets</span>
            </div>
            <div className={styles.zoneBarTrack}>
              <div
                className={`${styles.zoneBarFill} ${above ? styles.zoneAbove : styles.zoneBelow}`}
                style={{ width: `${fillW}%` }}
              />
              <span className={styles.zoneBreakEven} style={{ left: `${breakEven * 100}%` }} aria-hidden />
            </div>
            <div className={`${styles.zoneRate} ${above ? styles.numWin : styles.numLoss}`}>
              {pct.toFixed(1)}%
            </div>
            <div className={`${styles.zonePL} ${z.unitsPL >= 0 ? styles.numWin : styles.numLoss}`}>
              {formatUnits(z.unitsPL)}
            </div>
          </div>
        );
      })}
      <div className={styles.zoneFoot}>
        Vertical mark = {(breakEven * 100).toFixed(1)}% break-even at -110.  Bars right of the
        line are profitable on the long run; left of it are net losers.
      </div>
    </div>
  );
}

/* ------------- T4.23 calibration plot ------------- */

function CalibrationPlot({ zones }: { zones: import("@/lib/roi").ZoneRoi[] }) {
  // Predicted probability per zone — coarse, hand-coded from the LR
  // classifier thresholds.  STRONG NRFI fires at p_nrfi >= 0.56, so use
  // the bin midpoint (0.65 say) as a rough predicted probability.
  // STRONG YRFI fires at p_nrfi < 0.44 → P(YRFI) >= 0.56 (0.65 mid).
  // LEAN equivalently maps to a thinner band.
  const predicted: Record<string, number> = {
    "STRONG NRFI": 0.65,
    "LEAN NRFI":   0.54,
    "LEAN YRFI":   0.54,
    "STRONG YRFI": 0.65,
  };
  const points = zones
    .filter((z) => z.bets > 0 && z.label in predicted)
    .map((z) => ({
      label: z.label,
      pred:  predicted[z.label],
      actual: z.hitRate,
      n:      z.bets,
    }));
  if (points.length === 0) {
    return <div className={styles.chartEmpty}>Not enough data to plot calibration yet.</div>;
  }
  const W = 480;
  const H = 320;
  const padL = 50, padR = 16, padT = 14, padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  // Both axes 0.30 → 0.80 (covers our practical zone range)
  const xMin = 0.30, xMax = 0.80;
  const yMin = 0.30, yMax = 0.80;
  const xFor = (v: number) => padL + ((v - xMin) / (xMax - xMin)) * innerW;
  const yFor = (v: number) => padT + innerH - ((v - yMin) / (yMax - yMin)) * innerH;
  // Diagonal y=x reference
  const diag = `M ${xFor(xMin)} ${yFor(yMin)} L ${xFor(xMax)} ${yFor(xMax)}`;
  const ticks = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8];

  return (
    <div className={styles.calibPlot}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.chartSvg}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Calibration plot: predicted probability vs actual hit rate"
      >
        {/* Grid */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={xFor(t)} x2={xFor(t)} y1={padT} y2={padT + innerH} className={styles.gridLine} />
            <line x1={padL} x2={padL + innerW} y1={yFor(t)} y2={yFor(t)} className={styles.gridLine} />
            <text x={xFor(t)} y={H - padB + 16} textAnchor="middle" className={styles.axisText}>
              {(t * 100).toFixed(0)}%
            </text>
            <text x={padL - 8} y={yFor(t) + 3} textAnchor="end" className={styles.axisText}>
              {(t * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        {/* Diagonal reference */}
        <path d={diag} className={styles.calibDiag} />
        {/* Zone points */}
        {points.map((p) => {
          const tone = p.actual > p.pred ? "above" : "below";
          return (
            <g key={p.label}>
              <line
                x1={xFor(p.pred)}
                y1={yFor(p.pred)}
                x2={xFor(p.pred)}
                y2={yFor(p.actual)}
                className={tone === "above" ? styles.calibStemAbove : styles.calibStemBelow}
              />
              <circle
                cx={xFor(p.pred)}
                cy={yFor(p.actual)}
                r={Math.max(5, Math.min(14, Math.sqrt(p.n) * 2))}
                className={tone === "above" ? styles.calibPointAbove : styles.calibPointBelow}
              >
                <title>{`${p.label}: predicted ${(p.pred*100).toFixed(0)}%, actual ${(p.actual*100).toFixed(1)}% (n=${p.n})`}</title>
              </circle>
              <text
                x={xFor(p.pred) + 12}
                y={yFor(p.actual) + 4}
                className={styles.calibLabel}
              >
                {p.label}
              </text>
            </g>
          );
        })}
        {/* Axis titles */}
        <text x={padL + innerW / 2} y={H - 4} textAnchor="middle" className={styles.calibAxisTitle}>
          Predicted hit rate
        </text>
        <text
          x={-padT - innerH / 2}
          y={14}
          textAnchor="middle"
          transform="rotate(-90)"
          className={styles.calibAxisTitle}
        >
          Actual hit rate
        </text>
      </svg>
      <div className={styles.calibFoot}>
        Dot size = number of resolved bets in that zone.  Above the diagonal = the model is
        underestimating the side; below = overestimating.  Aim for tight clustering on the line.
      </div>
    </div>
  );
}

/* ------------- table row ------------- */

function DayRow({ day, maxAbs }: { day: DayRecord; maxAbs: number }) {
  const isWin = day.units > 0;
  const isLoss = day.units < 0;
  const fillPct = (Math.abs(day.units) / maxAbs) * 100;

  return (
    <div className={styles.row}>
      <div className={styles.dateCell}>
        <span className={styles.dateMain}>{formatDate(day.date)}</span>
      </div>
      <div className={`${styles.right} ${isWin ? styles.numWin : isLoss ? styles.numLoss : styles.numFlat}`}>
        {formatUnits(day.units)}
      </div>
      <div
        className={`${styles.right} ${
          day.cumulative > 0 ? styles.numWin : day.cumulative < 0 ? styles.numLoss : styles.numFlat
        }`}
      >
        {formatUnits(day.cumulative)}
      </div>
      <div className={styles.barCell}>
        <div className={styles.distBar}>
          <div
            className={`${styles.distFill} ${isWin ? styles.distFillWin : styles.distFillLoss}`}
            style={{
              width: `${fillPct}%`,
              marginLeft: isWin ? "50%" : `${50 - fillPct}%`,
            }}
          />
          <div className={styles.distMid} />
        </div>
      </div>
    </div>
  );
}

/* ------------- helpers ------------- */

function formatUnits(n: number): string {
  if (n === 0) return "0.00u";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}u`;
}

function formatDate(iso: string): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    weekday: "short",
    timeZone: "UTC",
  });
}

function tileTone(units: number): string {
  if (units > 0) return styles.tileTonePos;
  if (units < 0) return styles.tileToneNeg;
  return "";
}
