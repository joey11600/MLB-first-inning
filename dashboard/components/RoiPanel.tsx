"use client";

import { useEffect, useState } from "react";
import type { RoiResponse, RoiWindow, ZoneRoi } from "@/lib/roi";
import styles from "./RoiPanel.module.css";

const WINDOWS: { key: RoiWindow; label: string }[] = [
  { key: "7d",     label: "Last 7d" },
  { key: "30d",    label: "Last 30d" },
  { key: "season", label: "Season" },
];

const BREAK_EVEN = 110 / 210; // 0.5238

export function RoiPanel({ initialDate, model = "v2" }: { initialDate: string; model?: "v2" | "v3" }) {
  const [window, setWindow] = useState<RoiWindow>("30d");
  const [data, setData]     = useState<RoiResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // T3.17 follow-up: when in v3 mode, pass model=v3 so the server can
    // aggregate pick_variants K rows instead of picks_2026 rows.
    // Currently /api/roi ignores the param -- v3 perf surfacing is
    // pending the server-side variant-aware aggregation work.
    const url = `/api/roi?window=${window}${initialDate ? `&date=${initialDate}` : ""}${model === "v3" ? "&model=v3" : ""}`;
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
  }, [window, initialDate, model]);

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <div className={styles.headLeft}>
          <span className={styles.eyebrow}>Performance</span>
          <span className={styles.title}>
            {model === "v3" ? "Bankroll @ DK · v3 shadow (showing v2 stats — v3 view coming soon)" : "Bankroll @ DK"}
          </span>
          {data && (
            <span className={styles.range}>
              {data.startDate} → {data.endDate}
              {data.daysIncluded > 0 && (
                <>
                  {" · "}
                  <span className={styles.rangeStrong}>
                    {data.gradedPicks} graded picks
                  </span>{" "}
                  across {data.daysIncluded}{" "}
                  {data.daysIncluded === 1 ? "day" : "days"}
                  {data.totalPicks > data.gradedPicks && (
                    <span className={styles.rangePending}>
                      {" "}
                      ({data.totalPicks - data.gradedPicks} pending)
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
        <TotalCard total={data?.total} />
        <div className={styles.zoneGrid}>
          {(data?.betZones ?? []).map((z) => (
            <ZoneCard key={z.label} zone={z} />
          ))}
          {data && data.betZones.length === 0 && (
            <div className={styles.emptyZone}>
              No graded bets in this window yet.
            </div>
          )}
        </div>

        {data && data.passZones.length > 0 && (
          <div className={styles.passRow}>
            <span className={styles.passEyebrow}>No-bet calls</span>
            {data.passZones.map((z) => (
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

function TotalCard({ total }: { total: ZoneRoi | undefined }) {
  if (!total) {
    return <div className={`${styles.totalCard} ${styles.totalCardEmpty}`} />;
  }
  const tone = totalTone(total);
  return (
    <div className={`${styles.totalCard} ${styles[`totalCard_${tone}`]}`}>
      <div className={styles.totalLeft}>
        <span className={styles.totalEyebrow}>Net P&amp;L · bet zones only</span>
        <span className={styles.totalUnits}>{formatUnits(total.unitsPL)}</span>
        <span className={styles.totalSub}>
          units across {total.bets} graded bets ({total.wins}W-{total.losses}L)
        </span>
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
          label="vs break-even"
          value={
            Number.isNaN(total.edgeVsBreakEven)
              ? "—"
              : signedPctText(total.edgeVsBreakEven)
          }
          tone={tone}
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

/**
 * Map a hit rate into 0..100 width on a bar where 50% maps to ~50% width
 * but the break-even line (52.38%) is rendered at 52% so users see how
 * close they are to it.
 */
function barWidthPct(hitRate: number): number {
  if (Number.isNaN(hitRate)) return 0;
  return Math.max(0, Math.min(100, hitRate * 100));
}
