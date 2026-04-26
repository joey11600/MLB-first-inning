"use client";

import styles from "./ControlPanel.module.css";

export type SideFilter = "ALL" | "NRFI" | "YRFI" | "PASS";
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
  { key: "ALL", label: "All" },
  { key: "LEAN+", label: "Lean+" },
  { key: "STRONG", label: "Strong" },
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

  return (
    <section className={styles.panel} aria-label="Control panel">
      <div className={styles.corner} data-pos="tl" aria-hidden />
      <div className={styles.corner} data-pos="tr" aria-hidden />
      <div className={styles.corner} data-pos="bl" aria-hidden />
      <div className={styles.corner} data-pos="br" aria-hidden />

      <div className={styles.row}>
        <div className={styles.field}>
          <label className="eyebrow" htmlFor="dateSelect">
            Slate date
          </label>
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
