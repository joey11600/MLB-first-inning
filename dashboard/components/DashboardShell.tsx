"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { BoardResponse, BoardRow } from "@/lib/types";
import { useSupabaseRealtime } from "@/lib/useSupabaseRealtime";
import { todayEtIso } from "@/lib/date";
import { ControlPanel, type Filters } from "./ControlPanel";
import { OpsHealthCard } from "./OpsHealthCard";
import { DemotionsBanner } from "./DemotionsBanner";
import { RoiPanel, resolveRecordDay } from "./RoiPanel";
import { DayReconcile } from "./DayReconcile";
import { ReliabilityCurve } from "./InsightCharts";
import { BoardTable } from "./BoardTable";
import { ChangeBanner } from "./ChangeBanner";
import { Ticker } from "./Ticker";
import type { RecFile } from "@/lib/season-record";
import { replayStakesFor } from "@/lib/season-record";
import { nightFromBoard, nightFromRecord } from "@/lib/reconcile";
import { TonightsActionCard } from "./TonightsActionCard";
import { SettingsDropdown } from "./SettingsDropdown";
import styles from "./DashboardShell.module.css";

// 2026-07-28 redesign -- SURFACES REMOVED FROM THIS SHELL.
//
// 2026-07-30 CORRECTION. This block asserted on 2026-07-28 that
// StatusLine, ShadowPnlCard and SlateProjections "were DELETED outright
// -- files and stylesheets", and that SummaryStrip was "still live:
// OpsHealthCard and TonightsActionCard both import it. Do not delete
// it."  BOTH CLAIMS WERE FALSE.
//
// All four .tsx files and all four stylesheets were still on disk two
// days later, and the two "imports" of SummaryStrip were PROSE mentions
// inside comments, not import statements -- verified by grepping actual
// `from "..."` lines, which returned nothing for any of the four.
// ShadowPnlCard was additionally the sole caller of /api/shadow-pnl, so
// that route was live in production with no consumer.
//
// They are deleted NOW, for real: 8 files plus the orphaned route.
// Recover from git history if ever wanted back.
//
// WHY THIS MATTERS BEYOND TIDINESS: a comment that says code is gone
// when it is not is worse than no comment. It is exactly how the
// realPricedCumulativePL bug survived -- documentation asserting a
// state nobody re-checked. If you claim a deletion here, run the grep
// in the same commit.
//
//   SummaryStrip  -- "Games today" now reads off the ticker's
//                    `games shown/total`; the Avg-λ tile (mean AND its
//                    min–max range) moves into the ticker; the STRONG
//                    NRFI / STRONG YRFI distribution buckets are the
//                    hero card's side rows (same predicate); the Pass
//                    bucket is the hero card's "Passed" row plus the
//                    board's per-game pass reasons.
//                    GONE WITH NO REPLACEMENT: the small "NRFI n · YRFI n"
//                    pair under "Games today".  It counted every row with
//                    a side at ANY strength -- leans and games without
//                    lineups included -- so it was always larger than the
//                    STRONG counts beside it with nothing explaining why.
//   StatusLine    -- the footer.  DATE lives in the header and the
//                    ticker's right cap; GAMES shown/total lives in the
//                    ticker; SIDE / STRENGTH / SORT are the ControlPanel
//                    buttons themselves.  Its three NRFI/PASS/YRFI colour
//                    swatches are gone deliberately: colour no longer
//                    encodes side, so the legend would be wrong.
//                    Deleted for real 2026-07-30.
//   ShadowPnlCard -- was supposed to fold into DemotionsBanner as its
//                    expanded body.  That never happened, and the file sat
//                    unmounted for three months.  Deleted for real
//                    2026-07-30, along with /api/shadow-pnl, which it
//                    was the only caller of.  The shadow-P&L question
//                    is answered by tools/cluster_shadow_pnl.py.
//   SlateProjections -- unmounted and unreferenced.  Deleted for real
//                    2026-07-30.
//
// None of these ships in the JavaScript bundle -- verified against
// .next/static/chunks before deleting, not assumed.

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
        // cascade through ChangeBanner / TonightsActionCard / RoiPanel /
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
  // Same resolution RoiPanel used to do internally.
  const recordDay = useMemo(
    () => resolveRecordDay(seasonRecord, data?.date ?? ""),
    [seasonRecord, data?.date],
  );

  const replayStakes = useMemo(
    () => replayStakesFor(seasonRecord, data?.date ?? ""),
    [seasonRecord, data?.date],
  );

  // ── D1 · ONE NIGHT OBJECT, RESOLVED ONCE ───────────────────────────
  // The ticker, the hero card and the money panel each used to call
  // nightFromBoard() themselves.  The comments said "one source", and
  // they were right about the FUNCTION but not about the DATASET: the
  // money panel preferred the nightly export (season_record.json) while
  // the ticker and the hero always read the live board.  On any date the
  // export covers, a grade or a manual-odds heal landing after the export
  // put one chain in the bar and a different chain in the table -- which
  // is exactly the "my bets are missing" reading this redesign exists to
  // kill.  Resolve it HERE, pass it down, and the three cannot disagree.
  //
  // Precedence is copied verbatim from what the money panel already did:
  // the REAL record per-day first (real captured prices, starts 5/07),
  // then PROJECTED per-day (April is projected-only), then the live board
  // for a date nothing has replayed yet -- i.e. tonight.
  const night = useMemo(() => {
    const realDay = seasonRecord?.real?.days.find((d) => d.date === data.date) ?? null;
    const projDay = seasonRecord?.projected?.days.find((d) => d.date === data.date) ?? null;
    return (
      nightFromRecord(realDay ?? projDay) ??
      nightFromBoard(data.rows, data.details, data.date)
    );
  }, [seasonRecord, data.rows, data.details, data.date]);

  // Plumbing health, fetched once here.  It drives TWO things: the small
  // dot on the header's "Generated ..." line (the healthy state, which no
  // longer needs a card of its own), and whether the full ops card is
  // mounted at all.  Previously the card always rendered -- a full-width
  // system chip wedged between the hero and the money numbers on a night
  // when nothing was wrong.
  const ops = useOpsHealth();
  const opsNeedsAttention = ops !== null && ops.status !== "ok";

  return (
    <>
      {/* Full-bleed sticky ticker, outside the max-width shell.
          It is the only surface that survives scrolling, so it is now the
          single home for every slate-wide total: the shared
          flagged/placed/settled chain, the average λ AND its min–max
          range (inherited from the deleted SummaryStrip), and the
          shown/total game count (inherited from the deleted StatusLine).
          `night` is passed in rather than recomputed -- see the D1 note
          above. */}
      <Ticker
        rows={data.rows}
        details={data.details}
        date={data.date}
        night={night}
        shown={displayed.length}
      />
      <main className={styles.shell}>
      {/* Tightened single-row header.  Brand on the left, slate hero
          centered, primary actions (history) and the settings dropdown
          on the right.  ThemeToggle + NotifyToggle live inside
          SettingsDropdown. */}
      <header className={styles.header}>
        <div className={styles.brand}>
          <div className={styles.mark} aria-hidden />
          <div className={styles.brandText}>
            {/* `brandSub` ("FIRST-INNING INTELLIGENCE") removed -- it was
                decoration taking 18px above the fold, and the mark plus
                the title already carry the identity. */}
            <div className={styles.brandTitle}>NRFI TERMINAL</div>
          </div>
        </div>

        <div className={styles.slateBlock}>
          <div className="eyebrow">Slate</div>
          <div className={styles.slateDate}>
            {formatDateHeader(data.date)}
          </div>
          {/* Freshness line.  "Is the data recent" and "is the plumbing
              alive" are the same question, so the healthy ops state is a
              dot and two ages here instead of a full-width card. */}
          <div className={styles.slateMeta}>
            Generated {formatRelativeTime(data.generatedAt)}
            <OpsDot health={ops} />
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

      {/* ══ 2026-07-28 redesign · THREE ZONES FOR THREE QUESTIONS ══
          The operator opens this page to answer exactly three things,
          and the page is now ordered to answer them in that order:

            ZONE 1 · TONIGHT        what do I bet, and how much
              hero card (amber rail) -> alerts -> filters -> the board
            ZONE 2 · PERFORMANCE    how am I doing
              inside RoiPanel: the system record, then the older ledger
            ZONE 3 · WHY            why did the system do that
              inside RoiPanel: the night-by-night reconciliation, below a
              hairline rule, on the page background rather than a card

          The one reorder from the previous layout: ChangeBanner moved UP
          from under the filters to the top of Zone 1.  A pick that
          flipped since he last looked is the most time-sensitive fact on
          the page and he has to act on it before first pitch; it was
          sitting below the fold underneath the money panel.  RoiPanel
          moves down for the mirror-image reason -- a record is not a
          thing you act on tonight. */}

      {/* ZONE 1 -- the hero.  `date` is passed now (it was not before, so
          the eyebrow read a bare "Tonight" even over a slate from three
          weeks ago).  `replayStakes` is passed so the card's "at risk"
          figure is the SUM OF THE STAKE CHIPS the operator can see on the
          rows below -- previously it added up the ledger's units, which
          are a flat 1.00 on every bet placed before Kelly sizing went
          live, so the two disagreed on any older slate. */}
      <TonightsActionCard
        rows={data.rows}
        details={data.details}
        date={data.date}
        night={night}
        replayStakes={replayStakes}
      />

      {/* ZONE 1 -- the alerts rail.  All three of these render nothing
          when nothing is wrong, so on a healthy night this band is zero
          pixels tall.  Order is deliberate: a pick that flipped, then a
          STRONG pick that is deliberately carrying no bet (he has to
          understand that BEFORE he reads the P&L or the P&L looks
          broken), then the plumbing. */}
      {/* FIX 3, second half -- RESERVE THE ROW SO NOTHING JUMPS.
          `opsNeedsAttention` is false during server rendering (the health
          fetch has not resolved yet), so the ops card was not in the SSR
          HTML at all.  It then appeared ~800ms after load and pushed
          everything below it -- including the money numbers -- down the
          page, mid-read.  Reserving the collapsed card's height while the
          health check is still in flight makes that shift 0px; once it
          resolves, either the card fills the reserved row or the row
          releases and the reservation is dropped.
          The inline style is deliberate: DashboardShell.module.css is
          owned by another change in flight, and this is one declaration
          that has to land with the collapse behaviour it belongs to. */}
      <div
        className={styles.alerts}
        style={ops === null ? { minHeight: 39 } : undefined}
      >
        <ChangeBanner changes={data.pickChanges} />

        {/* 2026-05-11: surfaces active cluster demotions so an
            experiment that should be 4 days doesn't quietly become
            permanent.  Renders nothing if no demotions are active. */}
        <DemotionsBanner />

        {/* Only when something is actually wrong.  The healthy state is
            the dot on the header's "Generated ..." line. */}
        {opsNeedsAttention && <OpsHealthCard />}
      </div>

      {/* RoiPanel USED TO RENDER HERE, above the board.
          It moved below the board on 2026-07-29 with the decision-first
          redesign, which the operator chose knowing the preview read
          "everything else below".

          THIS REVERSES AN EARLIER EXPLICIT REQUEST -- "the record is the
          second thing he wants to see, after what to bet tonight" -- so
          it is called out rather than quietly changed. The reasoning: on
          a phone, in the hour before first pitch, the performance panel
          is ~400px of analysis sitting between the decision and the
          slate the decision came from. Tonight's surfaces now sit
          together (plays, then the board), and everything that answers
          "how am I doing" follows them.

          To restore the old order, move the <RoiPanel> block back here.
          Nothing else depends on the position. */}

      {/* ZONE 1 -- the board's own controls, touching the board. */}
      <ControlPanel
        dates={data.availableDates}
        date={data.date}
        onDateChange={refetch}
        filters={filters}
        onFiltersChange={setFilters}
        loading={loading}
      />

      {/* ZONE 1 -- the slate, game by game.  Nothing is cut here; this is
          where density IS the product.  The hero says how many and how
          much in total, the board says which games and at what price. */}
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

      {/* ZONE 2 -- how am I doing.  Below the slate as of the
          decision-first redesign; see the note where it used to sit.
          `night` comes from the D1 hoist so this panel cannot describe a
          different night from the ticker and the decision card. */}
      <RoiPanel
        initialDate={data.date}
        rows={data.rows}
        details={data.details}
        seasonRecord={seasonRecord}
        night={night}
      />

      {/* ZONE 3 -- why did it do that.  Deepest reference material on
          the page, so it sits last. */}
      <div className="zoneWhy">
        <div className="zoneHead">
          <span className="eyebrow">Why the system did that</span>
        </div>
        {/* The inner stack owns the spacing between the two surfaces.
            `.zoneWhy` sets the rule and the padding above it but declares
            no gap, so without this the calibration card would sit flush
            against the reconciliation table. */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* CALIBRATION -- does the model's probability mean what it
              says?  This is the most literal possible answer to "why did
              the system do that", and it is the only chart on either page
              that can move a threshold, so it leads the zone.  It fetches
              its own data and renders nothing until that resolves, so it
              costs zero pixels on first paint and cannot delay the board
              above it. */}
          <ReliabilityCurve />
          <DayReconcile
            day={recordDay.day}
            tonight={night}
            selectedDate={data.date}
            sideFrom={recordDay.side?.from}
            sideTo={recordDay.side?.to}
          />
        </div>
      </div>

      {/* No footer.  The page ends on the reconciliation table -- the
          reference material -- rather than on a text restatement of the
          filter buttons that are visible 400px above it. */}
      </main>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   OPS HEALTH, COLLAPSED TO A DOT
   ───────────────────────────────────────────────────────────────────
   The full ops card used to render on every load -- "checking…" first,
   then a full-width "System Healthy · predict 4 min ago · live state
   just now · grade 1 h ago" chip sitting between the hero and the money
   numbers.  On a healthy night that is a whole band of screen spent
   saying nothing happened.

   It now collapses to a 7px dot and two ages on the header line that
   already reports data freshness, and the full card is mounted only when
   the status is not "ok" -- at which point it IS a tonight-action fact,
   because a broken predict cron means tonight's picks are wrong.

   Fetched here rather than inside the card so ONE fetch drives both the
   dot and the decision of whether to mount the card at all.
   ═══════════════════════════════════════════════════════════════════ */

/** Only the fields the header line needs.  The full card fetches (and
 *  types) the rest of the payload for itself when it mounts. */
interface OpsHealthSummary {
  status:               "ok" | "warn" | "degraded" | "unknown";
  minutesSincePredict:  number | null;
  minutesSinceGrade?:   number | null;
  errorsLast24h?:       number;
  gamesAwaitingGrade?:  number;
  reasons?:             string[];
}

const OPS_POLL_MS = 60_000;

function useOpsHealth(): OpsHealthSummary | null {
  const [health, setHealth] = useState<OpsHealthSummary | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/health-live", { cache: "no-store" });
        if (!res.ok) return;
        const json = (await res.json()) as OpsHealthSummary;
        if (!cancelled) setHealth(json);
      } catch {
        // Swallow: a failed health check must never blank the dashboard.
        // The dot keeps showing the last state it successfully read.
      }
    };
    void load();
    const id = window.setInterval(() => { void load(); }, OPS_POLL_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, []);
  return health;
}

/** Ages on the header line are deliberately terser than the ones inside
 *  the ops card ("4m", not "4 min ago") -- this line is a glance, not a
 *  report. */
function compactAge(min: number | null | undefined): string {
  if (min === null || min === undefined || !Number.isFinite(min)) return "—";
  if (min < 1)    return "now";
  if (min < 60)   return `${Math.round(min)}m`;
  if (min < 1440) return `${Math.floor(min / 60)}h`;
  return `${Math.floor(min / 1440)}d`;
}

function OpsDot({ health }: { health: OpsHealthSummary | null }) {
  const status = health?.status ?? "unknown";
  const title =
    health === null
      ? "Still checking whether the background jobs are running."
      : status === "ok"
        ? "The background jobs are running normally."
        : (health.reasons && health.reasons.length > 0
            ? health.reasons.join(" · ")
            : "Something in the background jobs needs a look — see the alert above the board.");

  return (
    <span className={styles.opsInline} data-status={status} title={title}>
      <span aria-hidden>·</span>
      <span className={styles.opsDot} aria-hidden />
      <span>predict {compactAge(health?.minutesSincePredict)}</span>
      <span aria-hidden>·</span>
      <span>grade {compactAge(health?.minutesSinceGrade)}</span>
    </span>
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
