"use client";

import { useEffect, useState } from "react";
import shellStyles from "./DashboardShell.module.css";

type Theme = "light" | "dark";

/**
 * LIGHT UNLESS THE OPERATOR SAID OTHERWISE (2026-08-03).
 *
 * This used to fall through to the OS preference, which meant an
 * operator on a dark-mode machine never saw the product's own theme.
 * Light is not a fallback here, it is the identity: PRODUCT.md's usage
 * scene is "phone in hand, early evening, ordinary indoor light... a
 * lit room, not a 2am incident call", and the newsprint palette was
 * chosen for that room. The dark variant still exists and the toggle
 * still reaches it; it is just no longer chosen on the operator's
 * behalf by an OS setting that knows nothing about the room.
 *
 * An EXPLICIT saved choice always wins, so anyone who toggles once is
 * never overridden.
 */
function readInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const saved = localStorage.getItem("nrfi-theme") as Theme | null;
  if (saved === "light" || saved === "dark") return saved;
  return "light";
}

function applyTheme(theme: Theme): void {
  const html = document.documentElement;
  html.classList.toggle("dark", theme === "dark");
  html.classList.toggle("light", theme === "light");
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const t = readInitialTheme();
    setTheme(t);
    applyTheme(t);
    setMounted(true);
  }, []);

  function toggle() {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    applyTheme(next);
    try {
      localStorage.setItem("nrfi-theme", next);
    } catch {
      /* ignore */
    }
  }

  // Render a placeholder during SSR mismatch window so hydration is clean
  if (!mounted) {
    return <span className={shellStyles.themeToggle} aria-hidden />;
  }

  return (
    <button
      type="button"
      className={shellStyles.themeToggle}
      onClick={toggle}
      aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
      title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
    >
      {theme === "light" ? (
        // Moon icon — switching TO dark
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      ) : (
        // Sun icon — switching TO light
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      )}
    </button>
  );
}
