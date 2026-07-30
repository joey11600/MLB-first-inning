/**
 * The T2.58 bet-lock window, in one place.
 *
 * A STRONG pick does not become a real bet the moment the model calls
 * it -- `tracker._apply_odds_to_row` only sets `bet_placed=Y` once the
 * slate is inside the lock window (default 60 minutes pre-game), so
 * lineups and weather have their final form before money commits.
 *
 * That makes "when does this lock" the single most time-sensitive fact
 * on the whole dashboard: it is the operator's deadline to get the bet
 * down, and the cron cadence bounds it to roughly 30-60 minutes of
 * notice. Extracted here 2026-07-29 from BoardRow so the decision card
 * at the top of the page and the board row lower down cannot drift
 * apart about when a bet closes.
 */

/** Minutes before first pitch that a STRONG pick commits.
 *  Mirrors `PICK_LOCK_AT_MIN_PREGAME` / `_pick_lock_minutes()` in
 *  tracker.py. Changing one without the other makes the UI lie. */
export const LOCK_MINUTES_PREGAME = 60;

/** Offset of America/New_York from UTC, in minutes, at `at`.
 *  Computed from the runtime's own tz database rather than hardcoding
 *  -240/-300, so DST transitions need no annual edit. */
function etOffsetMinutes(at: Date): number {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const parts = fmt.formatToParts(at);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  const etIso =
    `${get("year")}-${get("month")}-${get("day")}` +
    `T${get("hour")}:${get("minute")}:${get("second")}Z`;
  return (at.getTime() - Date.parse(etIso)) / 60_000;
}

/** First pitch as a real instant, or null when the time is a
 *  placeholder ("TBD", "After Game 1", ""). */
export function gameStartAt(gameTimeEt: string, slateDate: string): Date | null {
  if (!gameTimeEt || !slateDate || !gameTimeEt.includes(":")) return null;
  const cleaned = gameTimeEt.replace(/\s*ET\s*$/, "").trim();
  const m = cleaned.match(/^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?$/);
  if (!m) return null;
  let hour = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const ap = (m[3] || "").toUpperCase();
  if (ap === "PM" && hour < 12) hour += 12;
  if (ap === "AM" && hour === 12) hour = 0;
  const isoLocal =
    `${slateDate}T${String(hour).padStart(2, "0")}:${String(min).padStart(2, "0")}:00`;
  const offMin = etOffsetMinutes(new Date(`${slateDate}T12:00:00Z`));
  const ms = Date.parse(isoLocal + "Z") + offMin * 60_000;
  return Number.isFinite(ms) ? new Date(ms) : null;
}

/** The lock cutoff (first pitch minus `lockMin`), or null. */
export function computeLockAt(
  gameTimeEt: string,
  slateDate: string,
  lockMin: number = LOCK_MINUTES_PREGAME,
): Date | null {
  const start = gameStartAt(gameTimeEt, slateDate);
  return start ? new Date(start.getTime() - lockMin * 60_000) : null;
}

/** "8:38 PM ET" */
export function formatLockTime(d: Date): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric", minute: "2-digit", hour12: true,
  }).format(d) + " ET";
}

/** Whole minutes from now until `d`; negative once it has passed. */
export function minutesUntil(d: Date, now: Date = new Date()): number {
  return Math.round((d.getTime() - now.getTime()) / 60_000);
}

/** Countdown for a deadline the operator is racing.
 *  Deliberately coarse above an hour ("in 2h 05m") and precise below it
 *  ("in 14m"), because that is where the decision actually lives. */
export function formatCountdown(mins: number): string {
  if (mins < 0) return "locked";
  if (mins === 0) return "locking now";
  if (mins < 60) return `in ${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `in ${h}h ${String(m).padStart(2, "0")}m`;
}
