"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./ControlPanel.module.css";

export type SideFilter = "ALL" | "NRFI" | "YRFI" | "PASS";
// Strength filter values.
//   ALL    : every row regardless of strength
//   STRONG : STRONG NRFI + STRONG YRFI only (the rows actually bet)
//   LEAN+  : STRONG + LEAN (Phase 1.3, 2026-05-12, reactivated LEAN as
//            track-only; LEAN picks are logged with bet_placed=N for
//            the 60-bet break-even analysis).
export type StrengthFilter = "ALL" | "STRONG" | "LEAN+";
export type SortKey = "lambda-desc" | "lambda-asc" | "nrfi-desc" | "yrfi-desc" | "rank";

export interface Filters {
  side: SideFilter;
  strength: StrengthFilter;
  sort: SortKey;
  query: string;
}

const SIDE_OPTIONS: { key: SideFilter; label: string; tone?: string }[] = [
  { key: "ALL", label: "All" },
  { key: "NRFI", label: "NRFI", tone: "nrfi" },
  { key: "PASS", label: "Pass", tone: "pass" },
  { key: "YRFI", label: "YRFI", tone: "yrfi" },
];

const STRENGTH_OPTIONS: { key: StrengthFilter; label: string }[] = [
  { key: "ALL",    label: "All" },
  { key: "STRONG", label: "Strong only" },
];

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "yrfi-desc", label: "P(YRFI) high → low" },
  { key: "nrfi-desc", label: "P(NRFI) high → low" },
  { key: "lambda-desc", label: "λ high → low" },
  { key: "lambda-asc", label: "λ low → high" },
  { key: "rank", label: "Board rank" },
];

export function ControlPanel({
  dates,
  date,
  onDateChange,
  filters,
  onFiltersChange,
  loading,
}: {
  dates: string[];
  date: string;
  onDateChange: (d: string) => void;
  filters: Filters;
  onFiltersChange: (f: Filters) => void;
  loading: boolean;
}) {
  const currentIdx = dates.indexOf(date);
  const prevDate = currentIdx >= 0 && currentIdx < dates.length - 1 ? dates[currentIdx + 1] : null;
  const nextDate = currentIdx > 0 ? dates[currentIdx - 1] : null;

  // "Today" in Eastern Time -- the predictor's authoritative timezone.
  // Used to flag past slates with a "PAST" chip and live slates with "LIVE".
  const todayET = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const dateState: "live" | "past" | "future" =
    !date ? "live" :
    date === todayET ? "live" :
    date < todayET ? "past" : "future";

  return (
    <section className={styles.panel} aria-label="Control panel">
      <div className={styles.row}>
        <div className={styles.field}>
          <label className="eyebrow" htmlFor="dateSelect">
            Slate date
          </label>
          <div className={styles.dateRow}>
            <div className={styles.dateCluster}>
              <button
                type="button"
                className={styles.navBtn}
                onClick={() => prevDate && onDateChange(prevDate)}
                disabled={!prevDate || loading}
                aria-label="Previous date"
              >
                ◂
              </button>
              <select
                id="dateSelect"
                className={styles.select}
                value={date}
                onChange={(e) => onDateChange(e.target.value)}
                disabled={loading || dates.length === 0}
              >
                {dates.length === 0 && <option value="">No boards</option>}
                {dates.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className={styles.navBtn}
                onClick={() => nextDate && onDateChange(nextDate)}
                disabled={!nextDate || loading}
                aria-label="Next date"
              >
                ▸
              </button>
            </div>
            {dateState === "live" && (
              <span className={styles.dateBadge} data-tone="live" aria-label="Viewing today's slate">
                <span className={styles.dateBadgeDot} aria-hidden />
                LIVE
              </span>
            )}
            {dateState === "past" && (
              <button
                type="button"
                className={styles.dateBadgeBtn}
                data-tone="past"
                onClick={() => dates.includes(todayET) && onDateChange(todayET)}
                disabled={!dates.includes(todayET) || loading}
                title={dates.includes(todayET) ? "Jump to today's slate" : "No live slate available"}
              >
                PAST · {pastDelta(date, todayET)}
              </button>
            )}
            {dateState === "future" && (
              <span className={styles.dateBadge} data-tone="future" aria-label="Future slate">
                SCHEDULED
              </span>
            )}
          </div>
        </div>

        <div className={styles.divider} aria-hidden />

        <div className={styles.field}>
          <span className="eyebrow">Side</span>
          <div className={styles.segGroup} role="tablist">
            {SIDE_OPTIONS.map((o) => {
              const active = filters.side === o.key;
              return (
                <button
                  key={o.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  data-tone={o.tone ?? "neutral"}
                  className={`${styles.seg} ${active ? styles.segOn : ""}`}
                  onClick={() => onFiltersChange({ ...filters, side: o.key })}
                >
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className={styles.divider} aria-hidden />

        <div className={styles.field}>
          <span className="eyebrow">Strength</span>
          <div className={styles.segGroup} role="tablist">
            {STRENGTH_OPTIONS.map((o) => {
              const active = filters.strength === o.key;
              return (
                <button
                  key={o.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={`${styles.seg} ${active ? styles.segOn : ""}`}
                  onClick={() =>
                    onFiltersChange({ ...filters, strength: o.key })
                  }
                >
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className={styles.divider} aria-hidden />

        <div className={styles.field}>
          <label className="eyebrow" htmlFor="sortSelect">
            Sort
          </label>
          <select
            id="sortSelect"
            className={styles.select}
            value={filters.sort}
            onChange={(e) =>
              onFiltersChange({ ...filters, sort: e.target.value as SortKey })
            }
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.divider} aria-hidden />

        <div className={`${styles.field} ${styles.search}`}>
          <label className="eyebrow" htmlFor="searchInput">
            Find
          </label>
          <input
            id="searchInput"
            type="text"
            placeholder="team…"
            value={filters.query}
            onChange={(e) =>
              onFiltersChange({ ...filters, query: e.target.value })
            }
            className={styles.input}
            spellCheck={false}
            autoComplete="off"
          />
        </div>

        <div className={styles.runWrap}>
          <RunWorkflowControl />
        </div>

        <div className={styles.loadingWrap} aria-live="polite">
          {loading ? (
            <span className={styles.loading}>
              <span className={styles.sweep} />
              LOADING
            </span>
          ) : null}
        </div>
      </div>
    </section>
  );
}

/** "1 day ago" / "5 days ago" / "3 weeks ago" -- compact past-delta. */
function pastDelta(dateIso: string, todayIso: string): string {
  if (!dateIso || !todayIso) return "";
  const a = new Date(dateIso + "T12:00:00Z").getTime();
  const b = new Date(todayIso + "T12:00:00Z").getTime();
  const diffDays = Math.max(0, Math.round((b - a) / (1000 * 60 * 60 * 24)));
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "1d ago";
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.round(diffDays / 7)}w ago`;
  return `${Math.round(diffDays / 30)}mo ago`;
}

type RunStatus = "idle" | "dispatching" | "running" | "complete" | "error";

interface PollState {
  state: "pending" | "running" | "complete";
  currentStep: string;
  conclusion: string | null;
  runId: number;
  htmlUrl: string;
}

function RunWorkflowControl() {
  const router = useRouter();
  const [status, setStatus]   = useState<RunStatus>("idle");
  const [message, setMessage] = useState<string>("");
  const [runsUrl, setRunsUrl] = useState<string>("");
  const [progress, setProgress] = useState<string>(""); // friendly step label
  const dispatchedAtRef = useRef<number>(0);
  const runIdRef        = useRef<number | null>(null);
  const pollTimer       = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stop polling on unmount
  useEffect(() => () => {
    if (pollTimer.current) clearInterval(pollTimer.current);
  }, []);

  function stopPolling() {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  async function poll() {
    try {
      const params = runIdRef.current
        ? `runId=${runIdRef.current}`
        : `since=${dispatchedAtRef.current}`;
      const res = await fetch(`/api/run-job/status?${params}`, { cache: "no-store" });
      if (!res.ok) return;   // transient -- keep trying
      const data = (await res.json()) as PollState | { state: "pending"; currentStep: string };

      if ("runId" in data && data.runId) runIdRef.current = data.runId;
      if (data.currentStep) setProgress(data.currentStep);

      if (data.state === "complete") {
        stopPolling();
        const fullData = data as PollState;
        if (fullData.conclusion === "success") {
          setStatus("complete");
          setMessage("Action complete -- waiting for Vercel deploy...");
          // GitHub push -> Vercel deploy takes ~30-60s.  Poll the dashboard
          // data path to detect when fresh CSV is live, then refresh.
          waitForDeployAndRefresh();
        } else {
          setStatus("error");
          setMessage(`Action ${fullData.conclusion ?? "failed"}`);
        }
      }
    } catch {
      /* ignore transient errors; keep polling */
    }
  }

  function waitForDeployAndRefresh() {
    // Vercel rebuilds and deploys ~30-60s after push. Refresh after 45s,
    // and again at 90s if needed (covers slower deploys).
    setProgress("Deploying to dashboard (~45s)...");
    setTimeout(() => {
      setProgress("Loading fresh data...");
      router.refresh();
      // One more refresh in case the first hit cached HTML
      setTimeout(() => {
        router.refresh();
        setProgress("");
        setMessage("Dashboard updated");
      }, 5_000);
    }, 45_000);
  }

  async function trigger(action: "predict" | "grade") {
    stopPolling();
    runIdRef.current = null;
    setStatus("dispatching");
    setMessage("");
    setProgress("");
    try {
      const res = await fetch("/api/run-job", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("error");
        setMessage(data?.error ?? `HTTP ${res.status}`);
        return;
      }
      dispatchedAtRef.current = Number(data?.dispatchedAt) || Date.now();
      setRunsUrl(data?.runsUrl ?? "");
      setStatus("running");
      setMessage(`${action.toUpperCase()} dispatched`);
      setProgress("Queued...");
      // Start polling every 4s.  GitHub takes 3-8s to surface the run.
      pollTimer.current = setInterval(poll, 4_000);
      // Kick off the first poll immediately so the UI feels responsive
      void poll();
    } catch (e) {
      setStatus("error");
      setMessage(e instanceof Error ? e.message : "Network error");
    }
  }

  const busy = status === "dispatching" || status === "running";

  return (
    <div className={styles.runField}>
      <span className="eyebrow">Run job</span>
      <div className={styles.runRow}>
        <button
          type="button"
          className={styles.runBtn}
          data-tone="nrfi"
          onClick={() => trigger("predict")}
          disabled={busy}
          title="Generate today's slate via GitHub Actions"
        >
          {busy ? "..." : "Predict"}
        </button>
        <button
          type="button"
          className={styles.runBtn}
          data-tone="yrfi"
          onClick={() => trigger("grade")}
          disabled={busy}
          title="Grade today's results via GitHub Actions"
        >
          {busy ? "..." : "Grade"}
        </button>
      </div>

      {(status === "running" || status === "dispatching") && (
        <span
          className={`${styles.runMsg} ${styles.runMsgProgress}`}
          title={`${message}\n${progress}`}
        >
          <span className={styles.runSpinner} aria-hidden />
          {progress || message || "..."}
        </span>
      )}
      {status === "complete" && (
        <span className={`${styles.runMsg} ${styles.runMsgOk}`}>
          {progress || message}
        </span>
      )}
      {status === "error" && (
        <a
          className={`${styles.runMsg} ${styles.runMsgErr}`}
          href={runsUrl || undefined}
          target="_blank"
          rel="noreferrer"
          title={message}
        >
          {message.slice(0, 60)} {runsUrl ? "→ Actions" : ""}
        </a>
      )}
    </div>
  );
}
