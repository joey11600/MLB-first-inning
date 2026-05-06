"use client";

import { useEffect, useMemo, useState } from "react";
import type { BoardRow, GameDetail } from "@/lib/types";
import type { RoiResponse, RoiWindow, ZoneRoi } from "@/lib/roi";
import { aggregateTodayRoi, aggregateTodayClv } from "@/lib/roi-today";
import styles from "./RoiPanel.module.css";

/* ============================================================
   T3.21: consolidated performance card.

   Replaces the prior split where SummaryStrip carried a "Today P&L"
   tile and RoiPanel showed only 7d/30d/season -- the operator had
   to mentally segregate "today" from "window" stats.

   New: TODAY is just another option in the window toggle.  When
   selected, the panel reads rows + details from props and aggregates
   locally (no /api/roi round-trip).  Other windows keep fetching
   from the server.  Today's CLV summary is hosted inside the today
   view (also moved out of SummaryStrip).
   ============================================================ */

const WINDOWS: { key: RoiWindow; label: string }[] = [
  { key: "today",  label: "Today"    },
  { key: "7d",     label: "Last 7d"  },
  { key: "30d",    label: "Last 30d" },
  { key: "season", label: "Season"   },
];

interface RoiPanelProps {
  initialDate: string;
  /** T3.21: rows + details so the TODAY window can aggregate
   *  client-side without a server round-trip. */
  rows:        BoardRow[];
  details:     Record<string, GameDetail>;
}

export function RoiPanel({
  initialDate,
  rows,
  details,
}: RoiPanelProps) {
  const [window, setWindow] = useState<RoiWindow>("today");
  const [data, setData]     = useState<RoiResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // Today's CLV is computed once per rows/details change; only shown
  // when at least one STRONG bet has both opened + closing odds.
  const todayClv = useMemo(
    () => aggregateTodayClv(rows, details),
    [rows, details],
  );

  // TODAY window: compute locally from props.  Recomputes whenever
  // rows / details change so realtime grade updates show up
  // immediately without re-fetching anything.
  const todayData = useMemo<RoiResponse | null>(() => {
    if (window !== "today") return null;
    if (!initialDate) return null;
    return aggregateTodayRoi(rows, details, initialDate);
  }, [window, rows, details, initialDate]);

  // 7d / 30d / season windows: fetch from /api/roi.  TODAY is handled
  // above; we skip fetching when it's selected.
  useEffect(() => {
    if (window === "today") {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const url = `/api/roi?window=${window}${initialDate ? `&date=${initialDate}` : ""}`;
    fetch(url, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: RoiResponse | null) => {
        if (!cancelled) {
          setData(j);
          setLoading(false);
        }
      })
      .catch(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [window, initialDate]);

  // Effective render data: today's local agg or server fetch.
  const view = window === "today" ? todayData : data;

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <div className={styles.headLeft}>
          <span className={styles.eyebrow}>Performance</span>
          <span className={styles.title}>Bankroll @ DK</span>
          {view && (
            <span className={styles.range}>
              {window === "today"
                ? <>Tonight&rsquo;s slate</>
                : <>{view.startDate} → {view.endDate}</>}
              {view.daysIncluded > 0 && window !== "today" && (
                <>
                  {" · "}
                  <span className={styles.rangeStrong}>
                    {view.gradedPicks} graded picks
                  </span>{" "}
                  across {view.daysIncluded}{" "}
                  {view.daysIncluded === 1 ? "day" : "days"}
                  {view.totalPicks > view.gradedPicks && (
                    <span className={styles.rangePending}>
                      {" "}
                      ({view.totalPicks - view.gradedPicks} pending)
                    </span>
                  )}
                </>
              )}
              {window === "today" && view.totalPicks > 0 && (
                <>
                  {" · "}
                  <span className={styles.rangeStrong}>
                    {view.gradedPicks} graded
                  </span>
                  {view.totalPicks > view.gradedPicks && (
                    <span className={styles.rangePending}>
                      {" "}
                      ({view.totalPicks - view.gradedPicks} pending)
                    </span>
                  )}
                </>
              )}
            </span>
          )}
        </div>
        <div className={styles.windowToggle} role="tablist" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.key}
              role="tab"
              aria-selected={window === w.key}
              className={`${styles.windowBtn} ${window === w.key ? styles.windowBtnActive : ""}`}
              onClick={() => setWindow(w.key)}
              type="button"
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      <div className={`${styles.body} ${loading ? styles.loading : ""}`}>
        <TotalCard
          total={view?.total}
          window={window}
          clv={window === "today" ? todayClv : null}
        />
        <div className={styles.zoneGrid}>
          {(view?.betZones ?? []).map((z) => (
            <ZoneCard key={z.label} zone={z} />
          ))}
          {view && view.betZones.length === 0 && (
            <div className={styles.emptyZone}>
              {window === "today"
                ? "No graded bets tonight yet."
                : "No graded bets in this window yet."}
            </div>
          )}
        </div>

        {view && view.passZones.length > 0 && (
          <div className={styles.passRow}>
            <span className={styles.passEyebrow}>No-bet calls</span>
            {view.passZones.map((z) => (
              <span key={z.label} className={styles.passChip}>
                <span className={styles.passChipLabel}>{z.label}</span>
                <span className={styles.passChipCount}>{z.picks}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function TotalCard({
  total,
  window,
  clv,
}: {
  total:  ZoneRoi | undefined;
  window: RoiWindow;
  clv:    { avgPp: number; n: number } | null;
}) {
  if (!total) {
    return <div className={`${styles.totalCard} ${styles.totalCardEmpty}`} />;
  }
  const tone = totalTone(total);
  const subText = total.bets > 0
    ? `units across ${total.bets} graded bets (${total.wins}W-${total.losses}L)`
    : window === "today"
      ? "no bets graded yet today"
      : "no graded bets in this window";

  // Third stat slot: window-specific.  TODAY shows the day's CLV
  // (moved out of SummaryStrip); other windows show edge vs the
  // -110 break-even rate, the existing reference.
  const thirdLabel = window === "today" ? "Tonight CLV" : "vs break-even";
  const thirdValue =
    window === "today"
      ? (clv == null ? "—" : signedPpText(clv.avgPp / 100))
      : (Number.isNaN(total.edgeVsBreakEven)
          ? "—"
          : signedPctText(total.edgeVsBreakEven));
  const thirdTone: "win" | "loss" | "neutral" =
    window === "today"
      ? (clv == null ? "neutral" : clv.avgPp > 0.5 ? "win" : clv.avgPp < -0.5 ? "loss" : "neutral")
      : tone;

  return (
    <div className={`${styles.totalCard} ${styles[`totalCard_${tone}`]}`}>
      <div className={styles.totalLeft}>
        <span className={styles.totalEyebrow}>
          Net P&amp;L · bet zones only
        </span>
        <span className={styles.totalUnits}>{formatUnits(total.unitsPL)}</span>
        <span className={styles.totalSub}>{subText}</span>
      </div>
      <div className={styles.totalRight}>
        <Stat
          label="Record"
          value={total.bets > 0 ? `${total.wins}-${total.losses}` : "—"}
          variant="record"
        />
        <Stat
          label="Hit rate"
          value={Number.isNaN(total.hitRate) ? "—" : pctText(total.hitRate)}
          tone={tone}
          variant="num"
        />
        <Stat
          label={thirdLabel}
          value={thirdValue}
          tone={thirdTone}
          variant="num"
        />
      </div>
    </div>
  );
}

function ZoneCard({ zone }: { zone: ZoneRoi }) {
  const tone = zoneTone(zone);
  const sideTone = zone.side === "NRFI" ? "nrfi" : zone.side === "YRFI" ? "yrfi" : "neutral";

  return (
    <div className={`${styles.zoneCard} ${styles[`zone_${sideTone}`]} ${styles[`tone_${tone}`]}`}>
      <header className={styles.zoneHead}>
        <span className={styles.zoneLabel}>{zone.label}</span>
        <span className={styles.zoneCount}>
          {zone.bets > 0 ? `${zone.wins}-${zone.losses}` : "—"}
        </span>
      </header>
      <div className={styles.zoneUnits}>
        {zone.bets > 0 ? formatUnits(zone.unitsPL) : "—"}
        <span className={styles.zoneUnitsLabel}>units</span>
      </div>
      <div className={styles.zoneSub}>
        {zone.bets > 0 ? (
          <>
            <span>{pctText(zone.hitRate)} hit</span>
            <span className={styles.zoneSep}>·</span>
            <span className={styles[`edge_${tone}`]}>
              {signedPctText(zone.edgeVsBreakEven)} vs BE
            </span>
          </>
        ) : (
          <span className={styles.zoneSubMuted}>
            {zone.picks} picks, ungraded
          </span>
        )}
      </div>
      <div className={styles.zoneBar}>
        <span
          className={styles.zoneBarFill}
          style={{ width: `${barWidthPct(zone.hitRate)}%` }}
          aria-hidden
        />
        <span className={styles.zoneBarBE} aria-hidden />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  variant = "num",
}: {
  label: string;
  value: string;
  tone?: "win" | "loss" | "neutral";
  variant?: "num" | "record";
}) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span
        className={`${styles.statValue} ${variant === "record" ? styles.statValueRecord : ""} ${
          tone ? styles[`tone_${tone}`] : ""
        }`}
      >
        {value}
      </span>
    </div>
  );
}

// ---------- helpers ----------

function totalTone(t: ZoneRoi): "win" | "loss" | "neutral" {
  if (t.bets === 0) return "neutral";
  if (t.unitsPL > 0.05) return "win";
  if (t.unitsPL < -0.05) return "loss";
  return "neutral";
}

function zoneTone(z: ZoneRoi): "win" | "loss" | "neutral" {
  if (z.bets === 0) return "neutral";
  if (z.unitsPL > 0.05) return "win";
  if (z.unitsPL < -0.05) return "loss";
  return "neutral";
}

function formatUnits(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  return `${sign}${Math.abs(n).toFixed(2)}`;
}

function pctText(p: number): string {
  if (Number.isNaN(p)) return "—";
  return `${(p * 100).toFixed(1)}%`;
}

function signedPctText(p: number): string {
  if (Number.isNaN(p)) return "—";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${(p * 100).toFixed(1)}pp`;
}

/** Format a probability already in 0..1 as signed percentage points. */
function signedPpText(p: number): string {
  if (!Number.isFinite(p)) return "—";
  const sign = p >= 0 ? "+" : "";
  return `${sign}${(p * 100).toFixed(2)}pp`;
}

/**
 * Map a hit rate into 0..100 width on a bar where 50% maps to ~50% width
 * but the break-even line (52.38%) is rendered at 52% so users see how
 * close they are to it.
 */
function barWidthPct(hitRate: number): number {
  if (Number.isNaN(hitRate)) return 0;
  return Math.max(0, Math.min(100, hitRate * 100));
}
