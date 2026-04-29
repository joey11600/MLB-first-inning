"use client";

/**
 * ChangeBanner — surfaces today's intraday pick flips above the board.
 *
 * Source: data/pick_changes.csv, written by tracker.log_picks() whenever
 * an hourly cron run flips a pre-game pick (e.g. STARTER PENDING ->
 * STRONG YRFI as lineups post, or STRONG YRFI -> PASS as the lambda
 * floor demotes a borderline call).
 *
 * Hidden when there are no changes for the slate.  Collapses by default
 * to "3 picks changed today" with a click-to-expand showing the full
 * old -> new transition for each.
 */

import { useState } from "react";
import type { PickChange } from "@/lib/types";
import styles from "./ChangeBanner.module.css";

export function ChangeBanner({ changes }: { changes: PickChange[] }) {
  const [expanded, setExpanded] = useState(false);

  if (!changes || changes.length === 0) return null;

  // Dedupe: a pick that flipped from PASS -> STRONG -> PASS over three
  // refresh cycles only really has TWO meaningful endpoints (its first
  // observed state and its current state).  Reduce to the latest entry
  // per game_pk since that's what the user actually cares about.
  const latestByGame = new Map<string, PickChange>();
  for (const c of changes) {
    const key = c.gamePk || `${c.awayTeam}@${c.homeTeam}`;
    if (!latestByGame.has(key)) latestByGame.set(key, c);  // changes already sorted newest-first
  }
  const flips = Array.from(latestByGame.values());

  // Categorize for the headline summary -- helps the user know whether
  // they need to act (a PASS becoming a real bet) vs just confirm
  // (a real bet becoming PASS).
  const newBets   = flips.filter((c) => isPass(c.oldPickLabel) && !isPass(c.newPickLabel));
  const droppedBets = flips.filter((c) => !isPass(c.oldPickLabel) && isPass(c.newPickLabel));
  const sideFlipped = flips.filter((c) => !isPass(c.oldPickLabel) && !isPass(c.newPickLabel)
                                       && c.oldPickLabel !== c.newPickLabel);

  return (
    <section className={styles.banner}>
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className={styles.summary}
        aria-expanded={expanded}
      >
        <span className={styles.dot} aria-hidden />
        <span className={styles.title}>
          {flips.length} pick{flips.length === 1 ? "" : "s"} changed today
        </span>
        <span className={styles.tally}>
          {newBets.length > 0     && <span className={styles.tallyAdd}>+{newBets.length} new</span>}
          {droppedBets.length > 0 && <span className={styles.tallyDrop}>−{droppedBets.length} dropped</span>}
          {sideFlipped.length > 0 && <span className={styles.tallyFlip}>↻{sideFlipped.length} flipped</span>}
        </span>
        <span className={styles.toggle}>{expanded ? "Hide" : "Show"}</span>
      </button>
      {expanded && (
        <ul className={styles.list}>
          {flips.map((c) => (
            <li key={c.gamePk || `${c.awayTeam}@${c.homeTeam}`} className={styles.item}>
              <span className={styles.matchup}>
                {c.awayTeam} @ {c.homeTeam}
              </span>
              <span className={styles.gameTime}>{c.gameTimeEt.replace(/\s*ET\s*$/, "") || ""}</span>
              <span className={styles.transition}>
                <ChangeChip label={c.oldPickLabel} variant="old" />
                <span className={styles.arrow} aria-hidden>→</span>
                <ChangeChip label={c.newPickLabel} variant="new" />
              </span>
              <span className={styles.timestamp} title={c.capturedAtUtc}>
                {formatRelTime(c.capturedAtUtc)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


function isPass(label: string): boolean {
  const u = label.toUpperCase();
  return u.startsWith("PASS");
}

/** Short relative timestamp ("12 min ago" / "2 h ago") for refresh times. */
function formatRelTime(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const ageSec = Math.max(0, (Date.now() - t) / 1000);
  if (ageSec < 60)    return `${Math.round(ageSec)}s ago`;
  if (ageSec < 3600)  return `${Math.round(ageSec / 60)} min ago`;
  if (ageSec < 86400) return `${Math.round(ageSec / 3600)} h ago`;
  return `${Math.round(ageSec / 86400)} d ago`;
}


function ChangeChip({ label, variant }: { label: string; variant: "old" | "new" }) {
  const tone = labelTone(label);
  return (
    <span className={`${styles.chip} ${styles[`chip_${tone}`]} ${variant === "old" ? styles.chipOld : ""}`}>
      {label}
    </span>
  );
}


function labelTone(label: string): "nrfi" | "yrfi" | "pass" | "pending" {
  const u = label.toUpperCase();
  if (u.includes("STARTER")) return "pending";
  if (u.includes("NRFI")) return "nrfi";
  if (u.includes("YRFI")) return "yrfi";
  return "pass";
}
