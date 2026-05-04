"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { BoardResponse, BoardRow } from "@/lib/types";
import { useSupabaseRealtime } from "@/lib/useSupabaseRealtime";
import { ControlPanel, type Filters } from "./ControlPanel";
import { SummaryStrip } from "./SummaryStrip";
import { OpsHealthCard } from "./OpsHealthCard";
import { RoiPanel } from "./RoiPanel";
import { BoardTable } from "./BoardTable";
import { ChangeBanner } from "./ChangeBanner";
import { Ticker } from "./Ticker";
import { StatusLine } from "./StatusLine";
import { ModelToggle, usePersistedModel } from "./ModelToggle";
import { TonightsActionCard } from "./TonightsActionCard";
import { SettingsDropdown } from "./SettingsDropdown";
import styles from "./DashboardShell.module.css";

// T3.18: Filter persistence helpers.  We persist via TWO mechanisms:
//   1. URL search params (shareable, bookmarkable, survives reloads)
//   2. localStorage (survives across sessions when user navigates away)
// On mount we read from URL first, fall back to localStorage, fall back
// to defaults.  On every filter change we write to BOTH.
const FILTER_STORAGE_KEY = "nrfi-dashboard-filters-v1";

const DEFAULT_FILTERS: Filters = {
  side: "ALL",
  strength: "ALL",
  sort: "yrfi-desc",
  query: "",
};

function readPersistedFilters(): Filters {
  if (typeof window === "undefined") return DEFAULT_FILTERS;
  try {
    const url = new URL(window.location.href);
    const fromUrl: Partial<Filters> = {};
    const side = url.searchParams.get("side");
    const strength = url.searchParams.get("strength");
    const sort  = url.searchParams.get("sort");
    const query = url.searchParams.get("query");
    if (side === "ALL" || side === "NRFI" || side === "YRFI" || side === "PASS")
      fromUrl.side = side;
    if (strength === "ALL" || strength === "STRONG" || strength === "LEAN+")
      fromUrl.strength = strength;
    if (sort === "lambda-desc" || sort === "lambda-asc" || sort === "nrfi-desc" || sort === "yrfi-desc" || sort === "rank")
      fromUrl.sort = sort;
    if (query) fromUrl.query = query;
    if (Object.keys(fromUrl).length > 0) {
      return { ...DEFAULT_FILTERS, ...fromUrl };
    }
    // Fallback: localStorage
    const stored = window.localStorage.getItem(FILTER_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as Partial<Filters>;
      return { ...DEFAULT_FILTERS, ...parsed };
    }
  } catch { /* ignore */ }
  return DEFAULT_FILTERS;
}

export function DashboardShell({ initial }: { initial: BoardResponse }) {
  const [data, setData] = useState<BoardResponse>(initial);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  // T3.17: v2/v3 model toggle (Variant K shadow tracking).  v2 is the
  // production calibrator and source-of-truth; v3 is experimental shadow.
  const [model, setModel] = usePersistedModel();

  // Hydrate filters once on mount from URL params + localStorage.
  // (Done in an effect so SSR-rendered HTML doesn't read window.)
  useEffect(() => {
    setFilters(readPersistedFilters());
  }, []);

  // Persist filters whenever they change -- both to URL (for share) and
  // to localStorage (for cross-session continuity).
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const url = new URL(window.location.href);
      // Only write non-default values to keep URL clean
      const setOrDelete = (k: string, v: string, def: string) => {
        if (v && v !== def) url.searchParams.set(k, v);
        else url.searchParams.delete(k);
      };
      setOrDelete("side", filters.side, "ALL");
      setOrDelete("strength", filters.strength, "ALL");
      setOrDelete("sort", filters.sort, "yrfi-desc");
      setOrDelete("query", filters.query.trim(), "");
      window.history.replaceState(null, "", url.toString());
      window.localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters));
    } catch { /* ignore */ }
  }, [filters]);

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

  // T4.18: query also matches pitcher names from details map.  Lets the
  // user type "Verlander" to find every game he starts (or has started)
  // across the slate, not just team abbreviations.
  const displayed = useMemo(() => filterAndSort(data.rows, filters, data.details), [
    data.rows,
    filters,
    data.details,
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

  // T4.20: Browser notifications on new pick flips.  Compares the
  // pickChanges array between data refetches; any new entries (newer
  // capturedAtUtc than what we've seen) trigger a system notification
  // IF the user has granted permission AND opted in via the toggle in
  // localStorage ("notifyOnFlip"="1").  Notification permission is
  // never requested automatically -- the user explicitly clicks the
  // bell button (rendered in the header alongside ThemeToggle).
  const seenChangesRef = useRef<Set<string>>(new Set());
  const seenInitialized = useRef(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const optedIn = window.localStorage.getItem("notifyOnFlip") === "1";
    const granted = "Notification" in window && Notification.permission === "granted";
    // Seed the "seen" set on first load so existing flips don't trigger
    // a wave of stale notifications -- only NEW flips that arrive after
    // page load should ping.
    const sig = (c: { capturedAtUtc: string; gamePk: string; newPickLabel: string }) =>
      `${c.capturedAtUtc}|${c.gamePk}|${c.newPickLabel}`;
    if (!seenInitialized.current) {
      data.pickChanges.forEach(c => seenChangesRef.current.add(sig(c)));
      seenInitialized.current = true;
      return;
    }
    if (!optedIn || !granted) return;
    for (const c of data.pickChanges) {
      const s = sig(c);
      if (seenChangesRef.current.has(s)) continue;
      seenChangesRef.current.add(s);
      try {
        new Notification(`${c.awayTeam} @ ${c.homeTeam}`, {
          body: `${c.oldPickLabel} → ${c.newPickLabel}`,
          tag: `nrfi-${c.gamePk}`,
          icon: "/favicon.ico",
        });
      } catch { /* notification API can throw on rare browsers; non-fatal */ }
    }
  }, [data.pickChanges]);

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
    // Poll cadence: 30s on a current-or-future date (live games may be
    // grading), 90s otherwise (historical view, no urgency).  T2.27 made
    // grade-today fire on every cron; the 30s poll catches the grade
    // within ~30s of the row updating in the CSV.  The previous 90s
    // cadence was tuned for the era when grade only ran at midnight.
    //
    // T2.31: with Phase 2's Supabase Realtime subscription wired below,
    // the *primary* update path is push (sub-second).  Polling stays as
    // a heartbeat fallback in case the WebSocket drops, the user is on
    // a date that hasn't been mirrored to Supabase yet, or env vars
    // aren't set (local dev w/o Supabase).
    const todayIso = new Date().toISOString().slice(0, 10);
    const isLive = (dataDateRef.current ?? "") >= todayIso;
    const id = window.setInterval(refresh, isLive ? 30_000 : 90_000);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // T2.31 / Phase 2 — Realtime subscription.  Fires a refetch the moment
  // any picks_<season> or pick_changes row changes for the displayed
  // date.  Auto-skipped on historical dates (nothing's mutating) and
  // when the env vars are missing (back-compat for the CSV-only path).
  // The hook itself manages the WebSocket lifecycle (subscribe / unsubscribe
  // on date change + unmount).
  useSupabaseRealtime({
    date: data.date || null,
    onChange: (table) => {
      // live_game_state changes flow through the existing /api/live-state
      // endpoint + useLiveGameState hook -- not the board API -- so we
      // intentionally don't refetch the board for those.  Picks /
      // pick_changes flips DO need a board refresh.
      if (table === "live_game_state") return;
      const d = dataDateRef.current;
      if (d) void refetch(d);
    },
  });

  return (
    <>
      {/* Full-bleed sticky ticker, outside the max-width shell */}
      <Ticker rows={data.rows} date={data.date} />
      <main className={styles.shell}>
      {/* Tightened single-row header.  Brand on the left, slate hero
          centered, primary actions (model toggle + history) and the
          settings dropdown on the right.  The MODEL meta block was
          dropped because the ModelToggle pill itself shows current
          state -- two surfaces saying the same thing was redundant.
          ThemeToggle + NotifyToggle moved into SettingsDropdown. */}
      <header className={styles.header}>
        <div className={styles.brand}>
          <div className={styles.mark} aria-hidden />
          <div className={styles.brandText}>
            <div className={styles.brandTitle}>NRFI TERMINAL</div>
            <div className={styles.brandSub}>
              FIRST-INNING INTELLIGENCE
            </div>
          </div>
        </div>

        <div className={styles.slateBlock}>
          <div className="eyebrow">Slate</div>
          <div className={styles.slateDate}>
            {formatDateHeader(data.date)}
          </div>
          <div className={styles.slateMeta}>
            Generated {formatRelativeTime(data.generatedAt)}
          </div>
        </div>

        <div className={styles.headerActions}>
          <ModelToggle model={model} onChange={setModel} />
          <a href="/history" className={styles.navLink} title="Bankroll history">
            <span className={styles.navLinkIcon} aria-hidden>▤</span>
            History
          </a>
          <SettingsDropdown notifyToggle={<NotifyToggle />} />
        </div>
      </header>

      {/* Post-redesign top-of-fold ordering:
          1. TonightsActionCard -- "what should I bet tonight" (NEW, hero)
          2. OpsHealthCard      -- system health surfaced if anything's wrong
          3. SummaryStrip       -- retrospective today-stats tiles
          4. RoiPanel           -- bankroll across 7d/30d/season
          5. ControlPanel       -- date + filters
          6. (changes / board / status)
       */}
      <TonightsActionCard
        rows={data.rows}
        details={data.details}
        model={model}
      />

      <OpsHealthCard />

      {/* T3.21: SummaryStrip slimmed -- no longer needs details since
          P&L/CLV moved into RoiPanel.  RoiPanel now receives rows +
          details so its TODAY window can aggregate locally without
          a server round-trip. */}
      <SummaryStrip rows={data.rows} model={model} />

      <RoiPanel
        initialDate={data.date}
        rows={data.rows}
        details={data.details}
        model={model}
      />

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
          date={data.date}
          model={model}
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

/** T4.20: Bell toggle for browser notifications on pick flips.  Click
 *  to enable: requests permission if needed, stores opt-in state in
 *  localStorage.  Click again to disable.  Visually a small icon
 *  button matching the ThemeToggle styling. */
function NotifyToggle() {
  const [enabled, setEnabled] = useState(false);
  const [supported, setSupported] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const has = "Notification" in window;
    setSupported(has);
    if (has) {
      const opted = window.localStorage.getItem("notifyOnFlip") === "1";
      setEnabled(opted && Notification.permission === "granted");
    }
  }, []);
  if (!supported) return null;
  const toggle = async () => {
    if (typeof window === "undefined") return;
    if (enabled) {
      window.localStorage.removeItem("notifyOnFlip");
      setEnabled(false);
      return;
    }
    let perm: NotificationPermission = Notification.permission;
    if (perm === "default") {
      perm = await Notification.requestPermission();
    }
    if (perm === "granted") {
      window.localStorage.setItem("notifyOnFlip", "1");
      setEnabled(true);
      try {
        new Notification("Notifications enabled", {
          body: "You'll get a ping when picks flip.",
          tag: "nrfi-test",
        });
      } catch { /* */ }
    }
  };
  return (
    <button
      type="button"
      onClick={toggle}
      className={styles.navLink}
      title={enabled ? "Notifications on (click to disable)" : "Click to enable pick-flip notifications"}
      aria-pressed={enabled}
    >
      <span className={styles.navLinkIcon} aria-hidden>
        {enabled ? "🔔" : "🔕"}
      </span>
    </button>
  );
}


function filterAndSort(
  rows: BoardRow[],
  f: Filters,
  details?: Record<string, import("@/lib/types").GameDetail>,
): BoardRow[] {
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
    if (q) {
      // T4.18: query matches team abbrs OR either pitcher's name
      // (e.g. typing "VERLANDER" surfaces every game he starts).
      // Detail lookup uses the same gamePk → away@home#N → away@home
      // chain as BoardTable so DH-2 rows resolve correctly.
      const detail = details
        ? (r.gamePk && details[r.gamePk])
          || details[`${r.away}@${r.home}#${r.gameNumber || 1}`]
          || details[`${r.away}@${r.home}`]
        : undefined;
      const hay = [
        r.away,
        r.home,
        detail?.away.pitcher.name ?? "",
        detail?.home.pitcher.name ?? "",
      ].join("|").toUpperCase();
      if (!hay.includes(q)) return false;
    }
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

/** Format a generated-at timestamp as a human relative time:
 *    "2m ago", "47m ago", "3h ago", "1d ago"
 *  Replaces the previous absolute "11:42 ET" rendering -- relative
 *  is more useful at a glance ("freshness") and degrades gracefully
 *  for stale slates.  Falls back to em-dash on null/unparseable. */
function formatRelativeTime(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const ageSec = Math.max(0, (Date.now() - t) / 1000);
  if (ageSec < 60)    return "just now";
  if (ageSec < 3600)  return `${Math.round(ageSec / 60)}m ago`;
  if (ageSec < 86400) return `${Math.round(ageSec / 3600)}h ago`;
  return `${Math.round(ageSec / 86400)}d ago`;
}
