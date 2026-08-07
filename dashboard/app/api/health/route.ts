/**
 * GET /api/health
 *
 * System status endpoint for monitoring + alerting.  Surfaces:
 *   - When the latest predict + grade ran (mtime of picks/board CSV)
 *   - How stale the most-recent slate is (minutes since last touch)
 *   - Recent cron failures from data/system_errors.csv
 *   - Counts of key files (boards/, picks_<year>.csv presence)
 *
 * Status levels:
 *   "OK"       - last predict <90 min ago AND no errors in last 24h
 *   "STALE"    - last predict 90-240 min ago OR no recent activity
 *   "DEGRADED" - errors logged in last 24h
 *   "BROKEN"   - last predict >240 min ago during prime hours (9am-9pm ET)
 *
 * Designed to be pinged by a monitor (Healthchecks.io, UptimeRobot,
 * cron-job.org) every 5-15 min.  Returns 200 always; consumers check
 * the JSON status field.
 */

import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";
import { parseCsv } from "@/lib/csv";
import { loadBoard } from "@/lib/board";
import { getServerSupabase } from "@/lib/supabase";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function dataDir(): string {
  const local = path.resolve(process.cwd(), "data");
  const parent = path.resolve(process.cwd(), "..", "data");
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fsSync = require("node:fs") as typeof import("node:fs");
    if (fsSync.existsSync(path.join(parent, "boards"))) return parent;
    if (fsSync.existsSync(path.join(local, "boards"))) return local;
  } catch { /* ignore */ }
  return parent;
}

async function safeStat(p: string): Promise<{ mtime: Date; size: number } | null> {
  try {
    const s = await fs.stat(p);
    return { mtime: s.mtime, size: s.size };
  } catch {
    return null;
  }
}

async function safeRead(p: string): Promise<string | null> {
  try { return await fs.readFile(p, "utf8"); } catch { return null; }
}

export async function GET() {
  const dir = dataDir();
  const now = new Date();

  // 1. Most recent board CSV (file presence; mtime is unreliable on Vercel
  // serverless -- the bundle FS resets mtimes to epoch on cold start).
  let boardsList: string[] = [];
  try { boardsList = (await fs.readdir(path.join(dir, "boards"))).filter(f => f.endsWith(".csv")); } catch { /* */ }
  boardsList.sort().reverse();
  const latestBoardName = boardsList[0] ?? null;
  const latestBoardStat = latestBoardName
    ? await safeStat(path.join(dir, "boards", latestBoardName))
    : null;

  // 2. picks_<year>.csv (presence; mtime unreliable, see above).
  const year = String(now.getFullYear());
  const picksStat = await safeStat(path.join(dir, `picks_${year}.csv`));

  // 2b. THE LAST-PREDICT TIMESTAMP MUST COME FROM SUPABASE FIRST.
  //
  // It used to be read only from the bundled data/thresholds.json's
  // writtenAtUtc. That was correct while EVERY push rebuilt the site --
  // the bundle was minutes old, so build age and data age were the same
  // number. Since T8.4 (2026-08-07) data-only commits no longer trigger
  // a build, and the two came apart: on 2026-08-07 this endpoint
  // reported lastPredictAt 17:44 (the bundled file) while the predictor
  // had run at 18:16.
  //
  // That is not a cosmetic staleness. `watchdog.check_dashboard()`
  // PAGES on BROKEN, and BROKEN triggers at >240 min during prime
  // hours -- so roughly four hours after the last CODE push this
  // endpoint would have invented a "Dashboard BROKEN" alert about a
  // perfectly healthy system, and kept doing it. It also fails the
  // other way: the 24h error window empties as the bundle ages, so a
  // real error storm could read OK.
  //
  // /api/health-live already had this right. Same query, same table.
  let lastPredictAt: Date | null = null;
  let freshnessSource: "supabase" | "bundle" | "none" = "none";
  try {
    const sb = getServerSupabase();
    if (sb) {
      const { data } = await sb
        .from(`picks_${year}`)
        .select("updated_at")
        .order("updated_at", { ascending: false })
        .limit(1);
      const iso = (data?.[0] as { updated_at?: string } | undefined)?.updated_at;
      const t = iso ? Date.parse(iso) : NaN;
      if (Number.isFinite(t)) {
        lastPredictAt = new Date(t);
        freshnessSource = "supabase";
      }
    }
  } catch { /* fall through to the bundled copy */ }

  // Bundled fallback, unchanged. Reaching it now means Supabase is
  // unconfigured or unreachable, so the figure describes the BUILD, not
  // the data -- `freshnessSource` says so in the response.
  if (!lastPredictAt) try {
    const tjson = await safeRead(path.join(dir, "thresholds.json"));
    if (tjson) {
      const obj = JSON.parse(tjson) as { writtenAtUtc?: string };
      // 2026-06-01: tolerate a historical malformed format that carried
      // BOTH an offset and a trailing Z (e.g. "2026-06-01T15:02:52+00:00Z"),
      // which Date.parse() rejects as NaN -> caused a false-BROKEN fall
      // back to the stale pick_changes.csv timestamp.  Strip the redundant
      // trailing Z when an explicit +/- offset is already present.  (The
      // predictor now emits a clean "...Z" form; this guard keeps old
      // bundled snapshots and any future producer slip from regressing.)
      const rawTs = (obj.writtenAtUtc ?? "").replace(/([+-]\d{2}:\d{2})Z$/, "$1");
      const t = Date.parse(rawTs);
      if (Number.isFinite(t)) {
        lastPredictAt = new Date(t);
        freshnessSource = "bundle";
      }
    }
  } catch { /* ignore */ }
  // Fallback: most recent captured_at_utc in pick_changes.csv (also
  // refreshed on every run that detects a flip; absent on quiet days).
  if (!lastPredictAt) {
    try {
      const changes = await safeRead(path.join(dir, "pick_changes.csv"));
      if (changes) {
        let max = 0;
        for (const r of parseCsv(changes)) {
          const t = Date.parse(r.captured_at_utc ?? "");
          if (Number.isFinite(t) && t > max) max = t;
        }
        if (max > 0) {
          lastPredictAt = new Date(max);
          freshnessSource = "bundle";
        }
      }
    } catch { /* ignore */ }
  }

  // 3. Recent system errors (last 24h) -- SUPABASE FIRST, same reason as
  // 2b. Read only from the bundle, this window empties out as the build
  // ages: a real error storm would read as a clean OK because the
  // bundled CSV predates it. The staleness fix above without this one
  // would leave the endpoint half-live and harder to reason about than
  // if it were wholly stale.
  let recentErrors: { capturedAtUtc: string; date: string; step: string; exitCode: string; message: string }[] = [];
  const errCutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  try {
    const sb = getServerSupabase();
    if (sb) {
      const { data } = await sb
        .from("system_errors")
        .select("captured_at_utc, date, step, exit_code, message")
        .gte("captured_at_utc", errCutoff.toISOString())
        .order("captured_at_utc", { ascending: false })
        .limit(100);
      if (data && data.length) {
        recentErrors = (data as Record<string, unknown>[]).map(r => ({
          capturedAtUtc: String(r.captured_at_utc ?? ""),
          date:          String(r.date ?? ""),
          step:          String(r.step ?? ""),
          // KEEP THE FULL MESSAGE: the known-noise classifier below
          // matches the TERMINAL "Fetch failed: HTTP Error 403" line,
          // which sits past char 200 of the logged stderr tail.
          exitCode:      String(r.exit_code ?? ""),
          message:       String(r.message ?? ""),
        })).slice(0, 50);
      }
    }
  } catch { /* fall through to the bundled CSV */ }

  const errorsRaw = recentErrors.length ? null : await safeRead(path.join(dir, "system_errors.csv"));
  if (errorsRaw) {
    const cutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    recentErrors = parseCsv(errorsRaw)
      .map(r => ({
        capturedAtUtc: r.captured_at_utc ?? "",
        date:          r.date ?? "",
        step:          r.step ?? "",
        exitCode:      r.exit_code ?? "",
        message:       r.message ?? "",
      }))
      .filter(r => {
        const t = Date.parse(r.capturedAtUtc);
        return Number.isFinite(t) && new Date(t) >= cutoff;
      })
      .sort((a, b) => b.capturedAtUtc.localeCompare(a.capturedAtUtc))
      .slice(0, 50);
  }

  // 3b. Known noise vs real errors (2026-08-05).  The GHA (backup) DK
  // scrape has been IP-blocked by DK's CDN since ~2026-05-04: every
  // hourly run logs a "Fetch failed: HTTP Error 403" row (~29/day)
  // while the Railway service does the actual odds capture.  That is
  // expected-and-routed-around, and counting it held this badge at
  // DEGRADED permanently -- months of alarm with no signal.  Match the
  // TERMINAL 403 line only: a scrape row with any other ending (read
  // timeout, the 2026-08-05 zero-markets id rotation, ...) still
  // counts as a real error.  The rows stay in system_errors.csv -- the
  // journal is untouched, only this badge's arithmetic ignores them.
  const isKnownBlockedScrape = (e: { step: string; message: string }) =>
    e.step === "scrape-dk-odds" &&
    e.message.includes("Fetch failed: HTTP Error 403");
  const knownNoise = recentErrors.filter(isKnownBlockedScrape);
  const realErrors = recentErrors.filter(e => !isKnownBlockedScrape(e));

  // 4. Compute staleness in minutes from the lastPredictAt source-of-truth.
  // We do NOT use file mtime (Vercel serverless FS unreliably resets it).
  const minutesSinceLatest = lastPredictAt
    ? Math.floor((now.getTime() - lastPredictAt.getTime()) / 60000)
    : Infinity;

  // 5. Determine prime hours in ET (9am-9pm)
  const etNow = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const etHour = etNow.getHours();
  const isPrime = etHour >= 9 && etHour < 21;

  // 5b. WHICH SOURCE IS ACTUALLY SERVING THE BOARD?
  //
  // loadBoard() is Supabase-first and falls back to the CSVs baked into
  // the build, SILENTLY -- a Supabase outage is swallowed and the stale
  // bundle is served, which from outside looks identical to a healthy
  // response. That was survivable while every push rebuilt the site and
  // the bundle was minutes old. Since T8.4 (2026-08-07) data commits no
  // longer build, so the bundle can be days stale and the fallback has
  // to announce itself.
  //
  // Best-effort: a failure to determine the source must never turn the
  // health endpoint itself into an error, because this endpoint is what
  // the watchdog reads.
  let boardSource: "supabase" | "csv" | "unknown" = "unknown";
  try {
    const b = await loadBoard(null);
    boardSource = b.source ?? "unknown";
  } catch {
    /* leave as unknown */
  }

  // 6. Status determination
  let status: "OK" | "STALE" | "DEGRADED" | "BROKEN" = "OK";
  const reasons: string[] = [];

  // Serving the build-time bundle during game hours means Supabase is
  // unreachable and the picks on screen may be days old. DEGRADED, not
  // BROKEN: the page still renders and the numbers were true once.
  if (boardSource === "csv" && isPrime) {
    if (status === "OK") status = "DEGRADED";
    reasons.push(
      "Board served from the build-time CSV fallback, not Supabase — " +
      "figures may be stale");
  }

  if (realErrors.length > 0) {
    status = "DEGRADED";
    reasons.push(`${realErrors.length} error(s) in last 24h`);
  }
  if (Number.isFinite(minutesSinceLatest)) {
    if (isPrime && minutesSinceLatest > 240) {
      status = "BROKEN";
      reasons.push(`No data refresh in ${minutesSinceLatest} min (prime hours)`);
    } else if (minutesSinceLatest > 90) {
      if (status === "OK") status = "STALE";
      reasons.push(`Last refresh ${minutesSinceLatest} min ago`);
    }
  } else {
    status = "BROKEN";
    reasons.push("No data files found");
  }

  return NextResponse.json(
    {
      status,
      reasons,
      checkedAt:           now.toISOString(),
      etHour,
      isPrimeHours:        isPrime,
      minutesSinceRefresh: Number.isFinite(minutesSinceLatest) ? minutesSinceLatest : null,
      lastPredictAt:    lastPredictAt ? lastPredictAt.toISOString() : null,
      latestBoard: latestBoardName
        ? {
            file: latestBoardName,
            sizeBytes: latestBoardStat?.size ?? null,
          }
        : null,
      latestPicks: picksStat
        ? {
            file: `picks_${year}.csv`,
            sizeBytes: picksStat.size,
          }
        : null,
      recentErrorCount24h: realErrors.length,
      recentErrors:        realErrors.slice(0, 10),  // cap response size
      // Known-blocked GHA scrape rows (403 wall) -- excluded from the
      // count above but reported so a debugging session can still see
      // the journal is alive.  See 3b.
      knownNoiseCount24h:  knownNoise.length,
      boardsAvailable:     boardsList.length,
      // "supabase" = live DB. "csv" = the build-time bundle, i.e.
      // the silent fallback. watchdog.check_board_source() pages on
      // "csv" during game hours.
      boardSource,
      // "supabase" = live DB. "bundle" = the build-time copy, i.e.
      // this number describes the BUILD age, not the data age.
      freshnessSource,
    },
    {
      status: 200,
      headers: { "Cache-Control": "no-store, max-age=0" },
    },
  );
}
