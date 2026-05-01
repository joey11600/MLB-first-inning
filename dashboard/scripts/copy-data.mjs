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

// 5. system_errors.csv -- recent cron failure log surfaced as a
//    "system status" indicator on the dashboard.  Defensive: always
//    populated by the workflow even on success days (just empty).
const errs = path.join(src, "system_errors.csv");
if (fs.existsSync(errs)) {
  fs.copyFileSync(errs, path.join(dest, "system_errors.csv"));
  copied += 1;
}

console.log(`[copy-data] copied ${copied} files from ${src} → ${dest}`);
