#!/usr/bin/env node
/**
 * check-kelly-parity.mjs -- fail the build if the dashboard's Kelly
 * sizing stops agreeing with tracker.py's.
 *
 * WHY THIS RUNS ON EVERY BUILD.
 * `lib/kelly-sim.ts` is a MIRROR of `tracker.kelly_stake_units`. tracker
 * sizes the real bet -- the number typed into a sportsbook, recorded as
 * units_risked, and published to paying subscribers. kelly-sim only
 * draws it. Two implementations of one money rule will drift, and when
 * they do nothing complains: both keep returning a plausible number.
 *
 * On 2026-08-06 they had already drifted. tracker rounds TWICE
 * (round(x,2) then round to whole units); kelly-sim rounded once. The
 * night's No.1 sized to 3.4975u, so Discord published "4 units" while
 * the board printed "STAKE 3.00u" for the same bet, and the operator
 * found it, not us. Any stake landing in [x.495, x.5) diverged the same
 * way, as did every exact half (Python rounds half-to-even, JavaScript
 * rounds half-up).
 *
 * The fixture is GENERATED FROM PYTHON, deliberately dense around the
 * rounding edges where the two disagreed. Regenerate it only when
 * tracker's sizing rule itself changes -- i.e. when a human has decided
 * to change real stakes:
 *
 *   python -c "..."   # see CHANGELOG 2026-08-06d for the generator
 *
 * If this check fails, the answer is almost never "update the fixture".
 * It is "kelly-sim.ts no longer matches the thing that sizes the bet".
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixturePath = path.join(here, "kelly-parity-fixture.json");

if (!fs.existsSync(fixturePath)) {
  console.error("[kelly-parity] fixture missing:", fixturePath);
  process.exit(1);
}

// Compile the real kelly-sim.ts rather than reimplementing it here --
// a hand-copied duplicate would pass this check while the shipped file
// was broken, which is the exact failure this guard exists to catch.
const outDir = path.join(here, ".kelly-parity-tmp");
fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });

// Invoke TypeScript's JS entry point with node rather than the .bin
// shim: on Windows the shim is a .cmd, and execFileSync cannot spawn a
// .cmd without a shell (EINVAL). This form is identical on Windows and
// on Vercel's Linux builders.
const tsc = path.join(here, "..", "node_modules", "typescript", "bin", "tsc");
try {
  execFileSync(process.execPath, [
    tsc,
    path.join(here, "..", "lib", "kelly-sim.ts"),
    "--outDir", outDir,
    "--module", "esnext",
    "--target", "es2022",
    "--moduleResolution", "bundler",
    "--skipLibCheck",
  ], { stdio: "pipe" });
} catch (err) {
  console.error("[kelly-parity] could not compile lib/kelly-sim.ts");
  console.error(String(err.stdout || err.message));
  process.exit(1);
}

const compiled = path.join(outDir, "kelly-sim.js");
const asModule = path.join(outDir, "kelly-sim.mjs");
fs.copyFileSync(compiled, asModule);

const { stakeUnitsFor } = await import(
  "file://" + asModule.split(path.sep).join("/"));

const { cases } = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
const bad = [];
for (const c of cases) {
  const got = stakeUnitsFor(c.p, c.american);
  if (Math.abs(got - c.expected) > 1e-9) {
    bad.push({ ...c, got });
  }
}

fs.rmSync(outDir, { recursive: true, force: true });

if (bad.length) {
  console.error(
    `\n[kelly-parity] FAILED -- lib/kelly-sim.ts disagrees with ` +
    `tracker.kelly_stake_units on ${bad.length} of ${cases.length} cases.\n`);
  for (const b of bad.slice(0, 15)) {
    console.error(
      `   p=${b.p} price=${b.american > 0 ? "+" : ""}${b.american}  ` +
      `tracker=${b.expected}  dashboard=${b.got}`);
  }
  if (bad.length > 15) console.error(`   ... and ${bad.length - 15} more`);
  console.error(
    `\n   tracker.py sizes the REAL bet. The dashboard only displays it.\n` +
    `   A disagreement means the dashboard is about to show a stake that\n` +
    `   is not what was wagered -- which is what shipped on 2026-08-06.\n` +
    `   Fix lib/kelly-sim.ts. Only regenerate the fixture if a human\n` +
    `   deliberately changed tracker's sizing rule.\n`);
  process.exit(1);
}

console.log(`[kelly-parity] ok -- ${cases.length} cases match tracker.py`);
