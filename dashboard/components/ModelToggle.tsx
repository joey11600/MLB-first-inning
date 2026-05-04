"use client";

/**
 * ModelToggle — T3.17.
 *
 * Pill-style toggle in the dashboard header for switching between the
 * production v2 calibrator and the experimental v3 calibrator (Variant K
 * shadow).  v2 is the default and remains the source-of-truth for
 * Telegram alerts, real bookkeeping, and what's actually being bet.
 * v3 view shows what would have happened if the model used the leak-free
 * truepit calibrator instead.
 *
 * Persists in localStorage across sessions.  On non-v2 selection,
 * shows a subtle "EXPERIMENTAL" tag so the operator never forgets they're
 * looking at shadow data.
 */

import { useEffect, useState, useCallback } from "react";
import styles from "./ModelToggle.module.css";

export type Model = "v2" | "v3";

const STORAGE_KEY = "nrfi-dashboard-model-v1";

export function readPersistedModel(): Model {
  if (typeof window === "undefined") return "v2";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "v2" || raw === "v3") return raw;
  } catch {
    /* swallow */
  }
  return "v2";
}

interface ModelToggleProps {
  model:    Model;
  onChange: (m: Model) => void;
}

export function ModelToggle({ model, onChange }: ModelToggleProps) {
  const setModel = useCallback((m: Model) => {
    onChange(m);
    try {
      window.localStorage.setItem(STORAGE_KEY, m);
    } catch {
      /* localStorage may be disabled; ignore */
    }
  }, [onChange]);

  return (
    <div
      className={styles.toggle}
      role="group"
      aria-label="Model view"
      title="Switch between v2 (production) and v3 (experimental shadow). v2 still drives Telegram alerts and real bookkeeping."
    >
      <button
        type="button"
        className={`${styles.option} ${model === "v2" ? styles.active : ""}`}
        aria-pressed={model === "v2"}
        onClick={() => setModel("v2")}
      >
        <span className={styles.optionLabel}>v2</span>
        <span className={styles.optionTag}>live</span>
      </button>
      <button
        type="button"
        className={`${styles.option} ${model === "v3" ? styles.active : ""}`}
        aria-pressed={model === "v3"}
        onClick={() => setModel("v3")}
      >
        <span className={styles.optionLabel}>v3</span>
        <span className={`${styles.optionTag} ${styles.optionTagShadow}`}>shadow</span>
      </button>
    </div>
  );
}

/** Hook: reads the persisted model on mount, returns [model, setModel].
 *  Use this in components that need to react to model changes from
 *  another tab. */
export function usePersistedModel(): [Model, (m: Model) => void] {
  const [model, setModel] = useState<Model>("v2");

  useEffect(() => {
    setModel(readPersistedModel());
  }, []);

  const setAndPersist = useCallback((m: Model) => {
    setModel(m);
    try { window.localStorage.setItem(STORAGE_KEY, m); } catch {}
  }, []);

  return [model, setAndPersist];
}
