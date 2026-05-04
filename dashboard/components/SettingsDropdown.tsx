"use client";

/**
 * SettingsDropdown -- secondary-action menu in the dashboard header.
 *
 * The previous header had four chip-style buttons in a row (ModelToggle,
 * History link, NotifyToggle, ThemeToggle).  The model toggle and the
 * History link are primary actions and stay visible in the header;
 * NotifyToggle (browser notifications opt-in) and ThemeToggle (light/
 * dark) are settings the operator touches once and forgets, so they
 * collapse into a single gear icon dropdown here.
 *
 * The component owns nothing except open/closed state and the popover
 * positioning -- it composes the existing toggles inside its menu so
 * their behavior, persistence, and prop signatures are unchanged.
 *
 * Click-outside / Escape closes the menu.  Focus moves to the first
 * menu item on open (keyboard accessibility).
 */

import { useEffect, useRef, useState } from "react";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./SettingsDropdown.module.css";

interface SettingsDropdownProps {
  /** NotifyToggle is rendered inline by DashboardShell (it owns
   *  notification permission state).  We accept it as a child so the
   *  dropdown can host whatever the parent passes without taking on
   *  a hard dependency on the notify code path. */
  notifyToggle?: React.ReactNode;
}

export function SettingsDropdown({ notifyToggle }: SettingsDropdownProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef   = useRef<HTMLButtonElement>(null);
  const menuRef      = useRef<HTMLDivElement>(null);

  // Close on click outside the dropdown root.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const root = containerRef.current;
      if (root && !root.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  // Close on Escape, return focus to the trigger.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Focus first focusable inside the menu when it opens, so keyboard
  // users land on a control without an extra Tab.
  useEffect(() => {
    if (!open) return;
    const m = menuRef.current;
    if (!m) return;
    const first = m.querySelector<HTMLElement>(
      "button, a, [tabindex]:not([tabindex='-1'])",
    );
    first?.focus();
  }, [open]);

  return (
    <div className={styles.root} ref={containerRef}>
      <button
        type="button"
        ref={triggerRef}
        className={`${styles.trigger} ${open ? styles.triggerOpen : ""}`}
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Settings"
        title="Settings"
      >
        <GearIcon />
      </button>

      {open && (
        <div
          ref={menuRef}
          className={styles.menu}
          role="menu"
          aria-label="Dashboard settings"
        >
          <div className={styles.menuEyebrow}>Settings</div>

          {notifyToggle && (
            <div className={styles.menuRow} role="none">
              <span className={styles.menuLabel}>Pick-flip notifications</span>
              <span className={styles.menuControl}>{notifyToggle}</span>
            </div>
          )}

          <div className={styles.menuRow} role="none">
            <span className={styles.menuLabel}>Appearance</span>
            <span className={styles.menuControl}>
              <ThemeToggle />
            </span>
          </div>

          <div className={styles.menuFoot}>
            <a
              href="/history"
              className={styles.menuFootLink}
              role="menuitem"
            >
              View bankroll history &rsaquo;
            </a>
          </div>
        </div>
      )}
    </div>
  );
}

function GearIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      width="18"
      height="18"
      aria-hidden
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}
