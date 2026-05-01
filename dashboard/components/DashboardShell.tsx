"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { BoardResponse, BoardRow } from "@/lib/types";
import { ControlPanel, type Filters } from "./ControlPanel";
import { SummaryStrip } from "./SummaryStrip";
import { RoiPanel } from "./RoiPanel";
import { BoardTable } from "./BoardTable";
import { ChangeBanner } from "./ChangeBanner";
import { Ticker } from "./Ticker";
import { StatusLine } from "./StatusLine";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./DashboardShell.module.css";

export function DashboardShell({ initial }: { initial: BoardResponse }) {
  const [data, setData] = useState<BoardResponse>(initial);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Filters>({
    side: "ALL",
    strength: "ALL",
    sort: "yrfi-desc",
    query: "",
  });

  async function refetch(date: string) {
    setLoading(true);
    try {
      const res = await fetch(`/api/board?date=${encodeURIComponent(date)}`, {
        cache: "no-store",
      });
      if (res.ok) {
        const json = (await res.json()) as BoardResponse;
        setData(json);
      }
    } finally {
      setLoading(false);
    }
  }

  const displayed = useMemo(() => filterAndSort(data.rows, filters), [
    data.rows,
    filters,
  ]);

  useEffect(() => {
    // keep URL in sync for shareability
    if (typeof window === "undefined" || !data.date) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("date") !== data.date) {
      url.searchParams.set("date", data.date);
      window.history.replaceState(null, "", url.toString());
    }
  }, [data.date]);

  // Auto-refresh whenever the tab regains focus or visibility -- catches the
  // common case where the user left the dashboard open across a cron run
  // (predict every 2h, grade overnight).  Without this, the SSR snapshot
  // baked at page-load stays frozen and grades / lineup updates appear
  // missing until the user remembers to hard-refresh.  Server-side has
  // cache-control: no-store on this route, so the fetch always hits fresh data.
  //
  // T2.6: keep `data.date` in a ref so the interval+listeners are
  // installed exactly ONCE on mount and torn down ONCE on unmount.  The
  // previous implementation had `[data.date]` in the deps array, which
  // recreated the interval every time the date refetched -- if a poll
  // fired during a render cycle, multiple intervals could accumulate.
  const dataDateRef = useRef(data.date);
  useEffect(() => { dataDateRef.current = data.date; }, [data.date]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const refresh = () => {
      const d = dataDateRef.current;
      if (document.visibilityState === "visible" && d) {
        void refetch(d);
      }
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    // Also poll on a timer (every 90s) so the same tab kept open during a
    // game updates the result column the moment grading lands.
    const id = window.setInterval(refresh, 90_000);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      {/* Full-bleed sticky ticker, outside the max-width shell */}
      <Ticker rows={data.rows} date={data.date} />
      <main className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <div className={styles.mark} aria-hidden />
          <div className={styles.brandText}>
            <div className={styles.brandTitle}>NRFI TERMINAL</div>
            <div className={styles.brandSub}>
              FIRST-INNING INTELLIGENCE · POISSON · PITCHER × OFFENSE × PARK
            </div>
          </div>
        </div>
        <div className={styles.meta}>
          <div>
            <div className="eyebrow">SLATE</div>
            <div className={styles.slateDate}>
              {formatDateHeader(data.date)}
            </div>
          </div>
          <div>
            <div className="eyebrow">GENERATED</div>
            <div className={`num ${styles.metaValue}`}>
              {data.generatedAt
                ? `${new Date(data.generatedAt).toLocaleString("en-US", {
                    hour: "2-digit",
                    minute: "2-digit",
                    month: "short",
                    day: "2-digit",
                    timeZone: "America/New_York",
                  })} ET`
                : "—"}
            </div>
          </div>
          <div>
            <div className="eyebrow">MODEL</div>
            <div className={`num ${styles.metaValue}`}>LR-v2 · calibrated</div>
          </div>
        </div>
        <div className={styles.headerActions}>
          <a href="/history" className={styles.navLink} title="Bankroll history">
            <span className={styles.navLinkIcon} aria-hidden>▤</span>
            History
          </a>
          <ThemeToggle />
        </div>
      </header>

      <SummaryStrip rows={data.rows} />

      <RoiPanel initialDate={data.date} />

      <ControlPanel
        dates={data.availableDates}
        date={data.date}
        onDateChange={refetch}
        filters={filters}
        onFiltersChange={setFilters}
        loading={loading}
      />

      <ChangeBanner changes={data.pickChanges} />

      <section className={styles.board}>
        <BoardTable
          rows={displayed}
          details={data.details}
          totalCount={data.rows.length}
          loading={loading}
          thresholds={data.thresholds}
        />
      </section>

      <StatusLine
        count={displayed.length}
        total={data.rows.length}
        filters={filters}
        date={data.date}
      />
      </main>
    </>
  );
}

function filterAndSort(rows: BoardRow[], f: Filters): BoardRow[] {
  const q = f.query.trim().toUpperCase();
  let out = rows.filter((r) => {
    if (f.side !== "ALL" && r.pickSide !== f.side) return false;
    if (f.strength === "STRONG" && r.pickStrength !== "STRONG") return false;
    if (
      f.strength === "LEAN+" &&
      r.pickStrength !== "STRONG" &&
      r.pickStrength !== "LEAN"
    )
      return false;
    if (q && !r.away.includes(q) && !r.home.includes(q)) return false;
    return true;
  });

  const cmp = {
    "lambda-desc": (a: BoardRow, b: BoardRow) => b.lambda - a.lambda,
    "lambda-asc": (a: BoardRow, b: BoardRow) => a.lambda - b.lambda,
    "nrfi-desc": (a: BoardRow, b: BoardRow) => b.nrfiPct - a.nrfiPct,
    "yrfi-desc": (a: BoardRow, b: BoardRow) => b.yrfiPct - a.yrfiPct,
    "rank": (a: BoardRow, b: BoardRow) => a.rank - b.rank,
  }[f.sort];
  out = [...out].sort(cmp);
  return out;
}

function formatDateHeader(iso: string): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  });
}
