"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { BoardResponse, BoardRow } from "@/lib/types";
import { useSupabaseRealtime } from "@/lib/useSupabaseRealtime";
import { todayEtIso } from "@/lib/date";
import { ControlPanel, type Filters } from "./ControlPanel";
import { SummaryStrip } from "./SummaryStrip";
import { OpsHealthCard } from "./OpsHealthCard";
import { DemotionsBanner } from "./DemotionsBanner";
import { ShadowPnlCard } from "./ShadowPnlCard";
import { RoiPanel } from "./RoiPanel";
import { BoardTable } from "./BoardTable";
import { ChangeBanner } from "./ChangeBanner";
import { Ticker } from "./Ticker";
import type { RecFile } from "@/lib/season-record";
import { replayStakesFor } from "@/lib/season-record";
import { StatusLine } from "./StatusLine";
import { TonightsActionCard } from "./TonightsActionCard";
import { SettingsDropdown } from "./SettingsDropdown";
import styles from "./DashboardShell.module.css";

// T-V21-LOCKIN-2026-05-06: removed ModelToggle (V2/V3 pill) and
// ShadowDeltaCard (V2-vs-V2.1 daily delta tile).  V2.1 is now the
// only production model; the V3 shadow comparison is no longer
// surfaced.  All `model` props on downstream components are
// dropped along with this.

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
  // T-V21-LOCKIN-2026-05-06: V2.1 is the only production model.
  // The v2/v3 ModelToggle was deleted along with the V3 shadow surface.

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

  // T-V21-2026-05-06f: content-fingerprint of just the user-visible
  // payload (date / rows / details / pickChanges / thresholds), but
  // NOT the server-generated `generatedAt` timestamp.  Prior behavior
  // re-rendered the whole tree on every poll/realtime event because
  // each /api/board response had a fresh `generatedAt`, so React saw
  // a new state object and dispatched a top-level update -- looked
  // like a "random refresh" to the user even when no row had moved.
  // Now we set state only when something semantically changed.
  function fingerprint(d: BoardResponse): string {
    return JSON.stringify({
      date:      d.date,
      rows:      d.rows,
      details:   d.details,
      changes:   d.pickChanges,
      thr:       d.thresholds,
      avail:     d.availableDates,
    });
  }
  const lastFingerprintRef = useRef<string>(fingerprint(initial));

  async function refetch(date: string) {
    setLoading(true);
    try {
      const res = await fetch(`/api/board?date=${encodeURIComponent(date)}`, {
        cache: "no-store",
      });
      if (res.ok) {
        const json = (await res.json()) as BoardResponse;
        const next = fingerprint(json);
        if (next !== lastFingerprintRef.current) {
          lastFingerprintRef.current = next;
          setData(json);
        }
        // Else: response semantically identical to current state.
        // Skip setData entirely so React doesn't trigger a re-render
        // cascade through ChangeBanner / SummaryStrip / RoiPanel /
        // BoardTable / Ticker just to repaint the same content.
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
    // T-V21-2026-05-06: strip any `?date=` left in the URL on mount.
    // Was: re-syncing data.date to the URL on every change, which made
    // a one-time visit to a past slate stick in the user's URL forever
    // (refresh / bookmark / restore-tab all re-loaded the stale date).
    // Now: URL is always clean.  The date picker in ControlPanel
    // navigates via React state (refetch); refresh returns to today.
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (url.searchParams.has("date")) {
      url.searchParams.delete("date");
      window.history.replaceState(null, "", url.toString());
    }

    // T-V21-2026-05-06b: BFCache eviction guard.  Chrome's back/forward
    // cache (BFCache) stores fully-rendered pages in memory and serves
    // them on `back` / `forward` / restored-tab without making a server
    // request -- which means our middleware + page redirect for `?date=`
    // never gets a chance to run.  When BFCache restores a page, the
    // `pageshow` event fires with `event.persisted === true`.  If the
    // restored URL still has `?date=`, force a hard navigation to "/"
    // so the user lands on a fresh, server-rendered page.  This is the
    // missing piece that was letting omnibox auto-suggest serve stale
    // HTML from past deployments.
    const handlePageShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      const u = new URL(window.location.href);
      if (u.searchParams.has("date")) {
        u.searchParams.delete("date");
        window.location.replace(u.pathname + u.search);
      }
    };
    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, []);

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
    // ET-aware so the live polling cadence stays on for late-evening
    // ET games after 8 PM ET (when UTC has rolled to tomorrow).
    const todayIso = todayEtIso();
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
  //
  // T-V21-2026-05-06: debounced.  Multiple writers (Railway predictor
  // every 5 min + GHA cron + import_odds + live worker grade extension)
  // can all touch a single row in the same second when a game grades or
  // a lock window opens.  Without coalescing, every event fired its own
  // refetch and the user saw rows briefly disappear / reappear during
  // the in-between writes.  Now we wait 1.5s after the LAST event
  // before refetching, so a burst of N writes results in 1 refresh.
  const refetchTimerRef = useRef<number | null>(null);
  useSupabaseRealtime({
    date: data.date || null,
    onChange: (table) => {
      // live_game_state changes flow through the existing /api/live-state
      // endpoint + useLiveGameState hook -- not the board API -- so we
      // intentionally don't refetch the board for those.  Picks /
      // pick_changes flips DO need a board refresh.
      if (table === "live_game_state") return;
      if (refetchTimerRef.current !== null) {
        window.clearTimeout(refetchTimerRef.current);
      }
      refetchTimerRef.current = window.setTimeout(() => {
        refetchTimerRef.current = null;
        const d = dataDateRef.current;
        if (d) void refetch(d);
      }, 1500);
    },
  });
  useEffect(() => {
    // Clean up any pending debounced refetch on unmount.
    return () => {
      if (refetchTimerRef.current !== null) {
        window.clearTimeout(refetchTimerRef.current);
      }
    };
  }, []);

  // THE season record, fetched ONCE here and shared. It drives both the
  // board's quarter-Kelly stake chips and the performance panel; fetching
  // it in each would pull ~375 KB twice and let the two surfaces drift.
  const [seasonRecord, setSeasonRecord] = useState<RecFile | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/season-record", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: RecFile | null) => { if (!cancelled && j) setSeasonRecord(j); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  // What the CURRENT model would stake on the slate being viewed.
  const replayStakes = useMemo(
    () => replayStakesFor(seasonRecord, data?.date ?? ""),
    [seasonRecord, data?.date],
  );

  return (
    <>
      {/* Full-bleed sticky ticker, outside the max-width shell */}
      {/* `details` is required now: the ticker renders the SHARED
          flagged/placed/settled string from lib/reconcile instead of
          inventing its own count, and settlement lives on the detail. */}
      <Ticker rows={data.rows} details={data.details} date={data.date} />
      <main className={styles.shell}>
      {/* Tightened single-row header.  Brand on the left, slate hero
          centered, primary actions (history) and the settings dropdown
          on the right.  ThemeToggle + NotifyToggle live inside
          SettingsDropdown. */}
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
          <a
            href="/history"
            className={styles.navLink}
            title="Bankroll history"
          >
            <span className={styles.navLinkIcon} aria-hidden>▤</span>
            History
          </a>
          <SettingsDropdown notifyToggle={<NotifyToggle />} />
        </div>
      </header>

      {/* 2026-07-28 redesign order (approved shape brief, PRODUCT.md):
          the page answers "what do I bet tonight, and how much" first,
          then the money record, then the full board; slate distribution
          and system plumbing drop below the fold.
          1. TonightsActionCard -- plays + stakes (hero)
          2. OpsHealth/Demotions -- render ONLY when something is wrong,
             so keeping them high costs nothing on a healthy day
          3. RoiPanel           -- System Record first inside, then ledger
          4. ControlPanel + ChangeBanner + Board -- the full slate
          5. SummaryStrip       -- slate distribution (retrospective)
          6. ShadowPnlCard      -- experiment plumbing
          7. StatusLine         -- footer
       */}
      <TonightsActionCard
        rows={data.rows}
        details={data.details}
      />

      <OpsHealthCard />

      {/* 2026-05-11: surfaces active cluster demotions so an
          experiment that should be 4 days doesn't quietly become
          permanent.  Renders nothing if no demotions are active. */}
      <DemotionsBanner />

      <RoiPanel initialDate={data.date} rows={data.rows} details={data.details} seasonRecord={seasonRecord} />

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
          replayStakes={replayStakes}
        />
      </section>

      {/* T3.21: SummaryStrip slimmed -- retrospective slate-distribution
          tiles. 2026-07-28: moved below the board; it was sitting
          between the hero and the money numbers. */}
      <SummaryStrip rows={data.rows} />

      {/* 2026-05-11: side-by-side comparison of placed (REAL) vs
          skipped (SHADOW) bets for each active cluster demotion.
          Renders nothing if no demotions are active. */}
      <ShadowPnlCard />

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
