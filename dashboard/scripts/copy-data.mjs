// Bundle ../data into ./data before `next build` so the CSVs ship with the
// Vercel deploy (the upload tarball starts at `dashboard/`, so sibling dirs
// are otherwise invisible to the build).
//
// IMPORTANT: only the dashboard-readable artifacts get copied -- copying the
// full `data/` tree blew past Vercel's 10MB upload limit once the cache
// directory crossed ~50MB.  The dashboard only reads boards/, picks_*.csv,
// and pick_changes.csv, so we whitelist those instead of mirroring the lot.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..");
const src = path.resolve(projectRoot, "..", "data");
const dest = path.resolve(projectRoot, "data");

if (!fs.existsSync(src)) {
  // T3.20: fail loudly when running on Vercel / CI (source must exist
  // for the dashboard to ship anything useful), but stay quiet when
  // run locally outside the repo (e.g. someone copies dashboard/ alone
  // for inspection).  Heuristic: if VERCEL or CI env var is set, the
  // build is in a CI environment and a missing src is a real problem.
  const inCI = !!(process.env.VERCEL || process.env.CI);
  if (inCI) {
    console.error(
      `[copy-data] FATAL: source data dir not found at ${src}.\n` +
      `  This bundle would ship with no boards or picks data.\n` +
      `  Verify the build is running from the repo root and that\n` +
      `  ../data/ exists relative to the dashboard directory.`,
    );
    process.exit(1);
  }
  console.log(`[copy-data] no ${src} — skipping (running outside repo?)`);
  process.exit(0);
}

// Wipe stale snapshot first so removed boards / pick changes don't linger.
fs.rmSync(dest, { recursive: true, force: true });
fs.mkdirSync(dest, { recursive: true });

let copied = 0;

// 1. Whole boards directory (small, every CSV is needed for the date picker).
const boardsSrc = path.join(src, "boards");
if (fs.existsSync(boardsSrc)) {
  const boardsDest = path.join(dest, "boards");
  fs.cpSync(boardsSrc, boardsDest, { recursive: true });
  copied += fs.readdirSync(boardsDest).length;
}

// 2. picks_<year>.csv -- season ledger, the biggest single file the dashboard
// reads.  Copy any picks_*.csv so future seasons just work.
for (const f of fs.readdirSync(src)) {
  if (/^picks_\d{4}\.csv$/.test(f)) {
    fs.copyFileSync(path.join(src, f), path.join(dest, f));
    copied += 1;
  }
}

// 3. pick_changes.csv -- intraday pick-flip journal for the ChangeBanner.
const changes = path.join(src, "pick_changes.csv");
if (fs.existsSync(changes)) {
  fs.copyFileSync(changes, path.join(dest, "pick_changes.csv"));
  copied += 1;
}

// 4. thresholds.json -- classifier thresholds the dashboard's tentative
//    classifier reads at request time so it never drifts from Python.
const thresholds = path.join(src, "thresholds.json");
if (fs.existsSync(thresholds)) {
  fs.copyFileSync(thresholds, path.join(dest, "thresholds.json"));
  copied += 1;
}

// fi_park_factors.json -- per-park p(no run in the 1st).  Read by /brief
// to say "a run scores here 58% of the time, the highest in baseball",
// which is usually the single biggest driver of a pick and the one an
// audience understands instantly.  Small (30 floats); without it the
// brief silently drops its ballpark reason on deployed builds.
const parkFactors = path.join(src, "fi_park_factors.json");
if (fs.existsSync(parkFactors)) {
  fs.copyFileSync(parkFactors, path.join(dest, "fi_park_factors.json"));
  copied += 1;
}

// season_record.json -- the walk-forward record shown as real profit.
const seasonRecord = path.join(src, "season_record.json");
if (fs.existsSync(seasonRecord)) {
  fs.copyFileSync(seasonRecord, path.join(dest, "season_record.json"));
  copied += 1;
}

// 5. system_errors.csv -- recent cron failure log surfaced as a
//    "system status" indicator on the dashboard.  Defensive: always
//    populated by the workflow even on success days (just empty).
const errs = path.join(src, "system_errors.csv");
if (fs.existsSync(errs)) {
  fs.copyFileSync(errs, path.join(dest, "system_errors.csv"));
  copied += 1;
}

// 6. T4.4 diagnostic outputs -- shadow_summary.csv is the daily
//    timeline of "V2 actual vs V2+T4.2 shadow" deltas.  Surfaced on
//    the dashboard so the operator sees at a glance whether T4.2 is
//    still producing positive delta vs the live model.  Only copy
//    the small summary file; the per-day detail CSVs are kept on
//    disk for jq investigation but don't ship with the bundle.
//
// T4.12 (also): bundle the LAST 7 DAYS of pick-reasoning JSON files
//    (data/diagnostics/picks/<date>.json from T4.6) so the dashboard
//    can surface "Why this pick?" feature contributions per row.
//    Older files stay on disk for historical investigation but don't
//    ship -- we only need recent days for the dashboard's row-expand
//    panel.
const diagSrc = path.join(src, "diagnostics");
if (fs.existsSync(diagSrc)) {
  const diagDest = path.join(dest, "diagnostics");
  fs.mkdirSync(diagDest, { recursive: true });
  const summary = path.join(diagSrc, "shadow_summary.csv");
  if (fs.existsSync(summary)) {
    fs.copyFileSync(summary, path.join(diagDest, "shadow_summary.csv"));
    copied += 1;
  }
  const driftAlerts = path.join(diagSrc, "drift_alerts.csv");
  if (fs.existsSync(driftAlerts)) {
    fs.copyFileSync(driftAlerts, path.join(diagDest, "drift_alerts.csv"));
    copied += 1;
  }

  // Per-pick reasoning JSONs (T4.6) -- bundle the most recent 7 files
  const picksSrc = path.join(diagSrc, "picks");
  if (fs.existsSync(picksSrc)) {
    const picksDest = path.join(diagDest, "picks");
    fs.mkdirSync(picksDest, { recursive: true });
    const allPicks = fs.readdirSync(picksSrc)
      .filter(f => /^\d{4}-\d{2}-\d{2}\.json$/.test(f))
      .sort()
      .slice(-7);
    for (const f of allPicks) {
      fs.copyFileSync(path.join(picksSrc, f), path.join(picksDest, f));
      copied += 1;
    }
  }
}

console.log(`[copy-data] copied ${copied} files from ${src} → ${dest}`);
