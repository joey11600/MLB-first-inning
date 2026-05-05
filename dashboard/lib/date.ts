/**
 * dashboard/lib/date.ts — ET-aware date helpers.
 *
 * MLB slates are organized by ET calendar day, not UTC.  During daylight
 * time UTC rolls to the next date at 8 PM ET; UTC-based "today" checks
 * misclassify late-evening ET games as historical and disable the live
 * pieces of the dashboard (Realtime subscriptions, polling, ROI windows).
 *
 * Use `todayEtIso()` everywhere we need the current MLB slate date on
 * the client.  Use `etIsoFromDate(d)` when you have a Date object and
 * need its ET calendar day in YYYY-MM-DD form.
 *
 * The `en-CA` locale is a deliberate choice: it formats numerically as
 * `YYYY-MM-DD`, which is exactly what `Intl.DateTimeFormat` produces
 * with the date-part options below — no manual string juggling.
 */

const ET_DATE_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Today's date in America/New_York as YYYY-MM-DD.  Pass a `now` for
 *  testing.  A typical use is `if (date < todayEtIso()) return;` to
 *  short-circuit live-update logic on historical slates. */
export function todayEtIso(now: Date = new Date()): string {
  return ET_DATE_FMT.format(now);
}

/** Convert any Date to its ET calendar day in YYYY-MM-DD form. */
export function etIsoFromDate(d: Date): string {
  return ET_DATE_FMT.format(d);
}
