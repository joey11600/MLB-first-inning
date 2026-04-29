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
