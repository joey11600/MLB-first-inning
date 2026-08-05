"use client";

/**
 * ControlPanel -- the board's own controls: date, side, strength, sort,
 * search. Nothing else.
 *
 * 2026-08-05 redesign, operator: "the controls are all weird". Three
 * things changed, all subtractive or translational:
 *
 *   1. The Run-job (GitHub Actions) buttons moved to the Settings menu
 *      (RunJobControl.tsx). They re-run the PIPELINE, not the view, and
 *      sat between the sort select and the search box where every
 *      date-change had to read past them.
 *   2. Sort options speak English. "λ high → low" assumed the reader
 *      knows lambda is expected first-inning runs; the operator has
 *      asked, in writing, not to be handed bare notation. Values are
 *      unchanged so persisted filters keep working.
 *   3. The side filter reads All · NRFI · YRFI · Pass. Pass sat in the
 *      middle of the two sides it is not one of.
 *
 * Date options render as "Wed · Aug 5" (value stays ISO). The raw ISO
 * list read like a database dump and made the picker feel broken.
 */

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
  { key: "YRFI", label: "YRFI", tone: "yrfi" },
  { key: "PASS", label: "Pass", tone: "pass" },
];

const STRENGTH_OPTIONS: { key: StrengthFilter; label: string }[] = [
  { key: "ALL",    label: "All" },
  { key: "STRONG", label: "Strong only" },
];

/* Keys are persisted in URLs and localStorage -- labels may change,
   keys may not. "Expected runs" is lambda said out loud. */
const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "yrfi-desc", label: "Best YRFI chance first" },
  { key: "nrfi-desc", label: "Best NRFI chance first" },
  { key: "lambda-desc", label: "Most expected runs first" },
  { key: "lambda-asc", label: "Fewest expected runs first" },
  { key: "rank", label: "Board rank" },
];

/** "2026-08-05" -> "Wed · Aug 5". The value stays ISO; only the label
 *  is for humans. */
function dateOptionLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const dt = new Date(Date.UTC(y, m - 1, d));
  const wd = dt.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  const md = dt.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return `${wd} · ${md}`;
}

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
    <section className={styles.panel} aria-label="Board controls">
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
                    {dateOptionLabel(d)}
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
            placeholder="team or pitcher…"
            value={filters.query}
            onChange={(e) =>
              onFiltersChange({ ...filters, query: e.target.value })
            }
            className={styles.input}
            spellCheck={false}
            autoComplete="off"
          />
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
