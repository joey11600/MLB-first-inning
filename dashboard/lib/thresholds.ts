/**
 * Single loader for data/thresholds.json.
 *
 * WHY THIS FILE EXISTS (2026-07-28)
 * ---------------------------------
 * There were TWO independent copies of this function -- one in board.ts
 * and one in board-supabase.ts -- each with its own hand-maintained
 * whitelist of forwarded fields. Both forwarded five core keys plus
 * lambdaNrfiCeiling and silently dropped everything else.
 *
 * That cost real behaviour twice over:
 *   * `strongYrfiP`, the tightened STRONG gate shipped 2026-07-27, never
 *     reached the dashboard. BoardRow.classifyTentative kept classifying
 *     with the OLD passLoP = 0.44 gate and disagreed with the predictor
 *     about which games are STRONG.
 *   * The Kelly block could not reach the stake chip at all.
 * And fixing board.ts alone changed nothing, because Supabase is
 * configured so board.ts delegates to board-supabase.ts, whose copy was
 * still dropping the fields.
 *
 * A whitelist is still the right shape -- the five core fields must be
 * present and numeric or the file is treated as missing, which keeps a
 * truncated file from silently degrading the classifier. But there must
 * be exactly ONE of it, and adding a key to the predictor must mean
 * adding it here and nowhere else.
 *
 * NOTE ON THE FILE ITSELF: dashboard/data/ is a COPY made by
 * scripts/copy-data.mjs, which runs on `npm run build` (prebuild) but
 * NOT on `npm run dev`. In dev the copy can therefore be stale; run
 * `node scripts/copy-data.mjs` after the predictor writes new keys.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { PickThresholds } from "./types";

/** Candidate locations, in preference order: the copy inside the app
 *  first (that is what a built deployment ships), then the repo-root
 *  data dir used when running from source. */
function candidatePaths(): string[] {
  return [
    path.resolve(process.cwd(), "data", "thresholds.json"),
    path.resolve(process.cwd(), "..", "data", "thresholds.json"),
  ];
}

export async function loadThresholds(): Promise<PickThresholds | undefined> {
  for (const p of candidatePaths()) {
    let raw: string;
    try {
      raw = await fs.readFile(p, "utf8");
    } catch {
      continue;
    }
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      continue;   // malformed file -> try the next candidate
    }

    const n = (v: unknown): number | null =>
      typeof v === "number" && Number.isFinite(v) ? v : null;

    // The five core fields are load-bearing: without them the tentative
    // classifier would silently fall back to hardcoded defaults, so a
    // truncated file must read as "missing" rather than "partly there".
    const core = {
      strongNrfiP:     n(obj.strongNrfiP),
      leanNrfiP:       n(obj.leanNrfiP),
      passLoP:         n(obj.passLoP),
      leanYrfiP:       n(obj.leanYrfiP),
      lambdaYrfiFloor: n(obj.lambdaYrfiFloor),
    };
    if (Object.values(core).some((v) => v == null)) continue;

    // Everything below is optional: an older deploy omits it, and the
    // consumer degrades (no stake chip, previous gate) rather than
    // breaking.
    const ceiling    = n(obj.lambdaNrfiCeiling);
    const strongYrfi = n(obj.strongYrfiP);
    const strongMax  = n(obj.strongYrfiMaxP);
    const kFraction  = n(obj.kellyFraction);
    const kBankroll  = n(obj.kellyBankrollUnits);
    const kMaxStake  = n(obj.kellyMaxStakeFrac);
    const kMaxDaily  = n(obj.kellyMaxDailyFrac);
    const kMinStake  = n(obj.kellyMinStakeUnits);
    const kCurBank   = n(obj.kellyCurrentBankrollUnits);

    return {
      ...core,
      ...(ceiling    != null ? { lambdaNrfiCeiling:  ceiling }    : {}),
      ...(strongYrfi != null ? { strongYrfiP:        strongYrfi } : {}),
      ...(strongMax  != null ? { strongYrfiMaxP:     strongMax }  : {}),
      ...(obj.kellyEnabled === true ? { kellyEnabled: true } : {}),
      ...(kFraction  != null ? { kellyFraction:      kFraction }  : {}),
      ...(kBankroll  != null ? { kellyBankrollUnits: kBankroll }  : {}),
      ...(kMaxStake  != null ? { kellyMaxStakeFrac:  kMaxStake }  : {}),
      ...(kMaxDaily  != null ? { kellyMaxDailyFrac:  kMaxDaily }  : {}),
      ...(kMinStake  != null ? { kellyMinStakeUnits: kMinStake }  : {}),
      ...(kCurBank   != null ? { kellyCurrentBankrollUnits: kCurBank } : {}),
    } as PickThresholds;
  }
  return undefined;
}
