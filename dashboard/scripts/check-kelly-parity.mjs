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
    // price-ladder.ts is checked too: its `passAt` is the "don't take
    // worse than" number PUBLISHED TO SUBSCRIBERS, and on 2026-08-07 it
    // said -162 while Discord said -165 for the same bet.
    path.join(here, "..", "lib", "price-ladder.ts"),
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

// ---- pass price ("don't take worse than"), the SECOND published number
// The stake check above only covers stakeUnitsFor. The ladder built on
// top of it drifted independently: TS solved analytically on the raw
// quarter-Kelly while Python walked a 5-cent grid on the ROUNDED stake,
// so Discord told subscribers they could lay a worse price than the
// dashboard allowed. Same fixture discipline, same failure mode.
const passFixturePath = path.join(here, "pass-price-fixture.json");
if (fs.existsSync(passFixturePath)) {
  const ladderMjs = path.join(outDir, "price-ladder.mjs");
  fs.copyFileSync(path.join(outDir, "price-ladder.js"), ladderMjs);
  fs.writeFileSync(
    ladderMjs,
    fs.readFileSync(ladderMjs, "utf8").replace(/["']\.\/kelly-sim["']/, '"./kelly-sim.mjs"'),
  );
  const { buildPriceLadder } = await import(
    "file://" + ladderMjs.split(path.sep).join("/"));
  const { cases: passCases } = JSON.parse(fs.readFileSync(passFixturePath, "utf8"));
  for (const c of passCases) {
    // `passAt` does not depend on the card price -- it is
    // worstPriceFor(p, 1) ceiled -- but buildPriceLadder returns null
    // when the CARD itself carries no edge. +400 has edge at every
    // probability in the fixture, so it exercises passAt without the
    // harness accidentally testing the card-price guard instead.
    // (-110 does not: at p=0.50 it is a losing price, the ladder is
    // correctly null, and the first version of this check reported 11
    // "failures" that were entirely its own fault.)
    const l = buildPriceLadder(c.p, 400);
    const got = l ? l.passAt : null;
    if (got !== c.passAt) {
      bad.push({ p: c.p, american: "passAt", expected: c.passAt, got });
    }
  }
}

fs.rmSync(outDir, { recursive: true, force: true });

if (bad.length) {
  // Name the RIGHT file. Two different mirrors are checked here, and a
  // failure message that blames kelly-sim.ts for a price-ladder drift
  // sends the reader to the wrong place -- which is its own small
  // version of the problem this guard exists to prevent.
  const stakeBad  = bad.filter(b => b.american !== "passAt");
  const ladderBad = bad.filter(b => b.american === "passAt");

  console.error(`\n[kelly-parity] FAILED -- the dashboard disagrees with Python ` +
                `on ${bad.length} case(s).\n`);
  if (stakeBad.length) {
    console.error(`  STAKE (lib/kelly-sim.ts vs tracker.kelly_stake_units) -- ${stakeBad.length}:`);
    for (const b of stakeBad.slice(0, 10)) {
      console.error(`   p=${b.p} price=${b.american > 0 ? "+" : ""}${b.american}  ` +
                    `python=${b.expected}  dashboard=${b.got}`);
    }
  }
  if (ladderBad.length) {
    console.error(`  PASS PRICE (lib/price-ladder.ts vs discord_broadcasts.pass_price) -- ${ladderBad.length}:`);
    for (const b of ladderBad.slice(0, 10)) {
      console.error(`   p=${b.p}  python=${b.expected}  dashboard=${b.got}`);
    }
  }
  console.error(
    `\n   Python is the side that sizes the real bet and writes the\n` +
    `   Discord message. The dashboard only displays. A disagreement\n` +
    `   means the two surfaces are about to publish different money\n` +
    `   instructions for the same wager -- 3u vs 4u on 2026-08-06, and\n` +
    `   "don't take worse than" -162 vs -165 on 2026-08-07.\n` +
    `   Fix the TypeScript. Regenerate a fixture only when a human has\n` +
    `   deliberately changed the Python rule.\n`);
  process.exit(1);
}

console.log(`[kelly-parity] ok -- ${cases.length} stake cases + pass-price cases match Python`);
