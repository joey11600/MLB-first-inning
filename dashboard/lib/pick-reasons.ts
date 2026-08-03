/**
 * REASONS — the model's evidence, rewritten as sentences.
 *
 * WHAT THIS IS FOR. The operator films a short video about the night's
 * top play and has to say why it is the top play. The material available
 * before this file existed was `top_drivers_t1`, which reads:
 *
 *     { name: "fi_park_nrfi_rate", z: -1.476, weight: -0.141,
 *       contribution: 0.2081 }
 *
 * That is unsayable. This file turns it into "a run scores in the first
 * here more often than at any park in baseball", which is the same fact.
 *
 * THE ORDERING IS THE MODEL'S, THE WORDING IS NOT. Where per-pick
 * diagnostics exist (`data/diagnostics/picks/<date>.json`, last 7 days
 * only), the model's own `contribution` magnitudes rank the reasons, so
 * the first thing the operator says really is the thing that moved the
 * number most. Where they do not exist the reasons fall back to a fixed
 * editorial order. Either way the SENTENCES are written here and the
 * FIGURES come from the ledger, so nothing is invented to fit a story.
 *
 * WHY "AGAINST" REASONS ARE FIRST-CLASS. The operator's opening instinct
 * for the 2026-08-03 card was that Tampa Bay had not scored in the first
 * all series and was therefore "due" for one. Two things are wrong with
 * that and both matter on camera: due-ness is not a thing, and the figure
 * argues AGAINST the YRFI bet the model made, not for it. The honest card
 * shows that stat, labels it as cutting against, and lets the operator
 * answer it out loud — which is a better video than pretending it is not
 * there, and is the only version that survives someone checking.
 */
import {
  LEAGUE,
  leanOf,
  type GameFiForm,
  type Lean,
  type Side,
} from "./first-inning-form";
import { cityOf, clubOf } from "./team-names";

export interface Reason {
  /** Stable key, also the sort tiebreak. */
  id: string;
  /** Two or three words. The chip label. */
  label: string;
  /** The figure, already in spoken form: "2 of 10", "58%", "3 straight". */
  figure: string;
  /** A full sentence the operator can read aloud verbatim. */
  sentence: string;
  /** The comparison that makes the figure mean something. */
  context: string | null;
  lean: Lean;
  /** Editorial rank, overridden by model contribution when available. */
  weight: number;
}

/** "2 of their last 10" reads aloud; "20.0%" does not. */
const ofN = (k: number, n: number) => `${k} of ${n}`;
const pct = (x: number) => `${Math.round(x * 100)}%`;

/**
 * Map a model feature name onto the reason it belongs to.
 *
 * Only the features that have a human counterpart are listed. Anything
 * unmapped simply does not reorder the list — it is not invented into a
 * sentence, because a sentence this file cannot write truthfully is
 * worse than no sentence.
 */
const FEATURE_TO_REASON: Record<string, string> = {
  fi_park_nrfi_rate: "park",
  home_p_last10_pitcher_nrfi: "home-pitcher",
  away_p_last10_pitcher_nrfi: "away-pitcher",
  home_p_last5_pitcher_nrfi: "home-pitcher",
  away_p_last5_pitcher_nrfi: "away-pitcher",
  home_top3c_obp: "home-bats",
  home_top3c_slg: "home-bats",
  home_top3c_iso: "home-bats",
  away_top3c_obp: "away-bats",
  away_top3c_slg: "away-bats",
  away_top3c_iso: "away-bats",
  home_top3_ops_vs_oppHand: "home-bats",
  away_top3_ops_vs_oppHand: "away-bats",
  era_gap_t1: "pitching-gap",
  era_gap_b1: "pitching-gap",
  home_fip: "home-pitcher",
  away_fip: "away-pitcher",
  home_xera: "home-pitcher",
  away_xera: "away-pitcher",
  home_whiff_pct_rank: "home-pitcher",
  away_whiff_pct_rank: "away-pitcher",
};

export interface ModelDriver {
  name: string;
  contribution: number;
}

/**
 * Build the reason list for one play.
 *
 * `drivers` is optional: pass the concatenated `top_drivers_t1` and
 * `top_drivers_b1` from the pick diagnostics to have the model's own
 * weighting decide the order.
 */
export function buildReasons(
  form: GameFiForm,
  pick: Side,
  drivers?: ModelDriver[] | null,
): { supports: Reason[]; against: Reason[]; neutral: Reason[] } {
  const out: Reason[] = [];
  const { away, home, awayTeam, homeTeam, park, h2h } = form;

  /* ---------------- the ballpark ----------------
     TWO FIGURES, TWO BASES, AND THE SENTENCE SAYS WHICH.
     `park.nrfiRate` is the model's park factor: a Bayesian-shrunk rate
     over LAST SEASON PLUS THIS ONE with a 50-game prior. `park.ledger`
     is the raw count of first innings played here THIS SEASON. For
     Colorado on 2026-08-03 those read 58% and 73%, fifteen points
     apart, and the page used to print them twenty lines from each
     other with neither one naming its basis. Read consecutively into a
     camera that is a self-contradiction, which is the exact failure
     this surface exists to prevent. So: the shrunk figure always says
     "across last season and this one", and when the season-alone
     figure diverges by more than eight points it is carried in the
     SAME sentence rather than left to ambush the reader later. */
  if (park.nrfiRate != null) {
    const yrfiHere = 1 - park.nrfiRate;
    const rankNote = parkTier(park.rank, park.parks);
    const led = park.ledger;
    const ledRate = led && led.games > 0 ? led.yrfi / led.games : null;
    const diverges = ledRate != null && Math.abs(ledRate - yrfiHere) > 0.08;
    out.push({
      id: "park",
      label: "The ballpark",
      figure: pct(yrfiHere),
      sentence:
        `Across last season and this one, a run has scored in the first inning ` +
        `${pct(yrfiHere)} of the time in ${cityOf(home)}, ${rankNote ?? "by the model's park factor"}.` +
        (diverges
          ? ` This season alone it has been ${ledRate! > yrfiHere ? "higher" : "lower"}: ${led!.yrfi} of the ${led!.games} played there, ${pct(ledRate!)}.`
          : ""),
      context: `league average is ${pct(LEAGUE.yrfi)}`,
      lean: leanOf(yrfiHere, LEAGUE.yrfi, true, pick),
      weight: 100,
    });
  }

  /* ---------------- each lineup's recent first innings ---------------- */
  for (const [side, t] of [
    ["away", awayTeam],
    ["home", homeTeam],
  ] as const) {
    const l = t.last10;
    if (l.games === 0) continue;
    const rate = l.scoredIn / l.games;
    out.push({
      id: `${side}-bats`,
      label: `${cityOf(t.team)} bats`,
      figure: ofN(l.scoredIn, l.games),
      sentence: `${cityOf(t.team)} have scored in the first inning in ${l.scoredIn} of their last ${l.games} games.`,
      context: `an average team does it about 3 times in 10`,
      lean: leanOf(rate, LEAGUE.teamScores, true, pick),
      weight: 80,
    });
    // A streak is the same fact said more sharply, so it only earns its
    // own line when it is long enough to be worth saying.
    if (t.scorelessStreak >= 3) {
      out.push({
        id: `${side}-streak`,
        label: `${cityOf(t.team)} drought`,
        figure: `${t.scorelessStreak} straight`,
        sentence: `${cityOf(t.team)} have gone ${t.scorelessStreak} straight games without scoring in the first.`,
        context:
          "a streak is not a signal: it does not make them due, and the model does not treat it as evidence",
        lean: leanOf(0, LEAGUE.teamScores, true, pick),
        weight: 55,
      });
    }
  }

  /* ---------------- the two starters ---------------- */
  for (const [side, p, opp] of [
    ["away", form.awayPitcher, home],
    ["home", form.homePitcher, away],
  ] as const) {
    if (!p) continue;
    const rate = p.modelLast10;
    if (rate == null) continue;
    const held = Math.round(rate * 10);
    out.push({
      id: `${side}-pitcher`,
      label: p.name,
      figure: ofN(held, 10),
      sentence: `${p.name}, starting for ${cityOf(side === "away" ? form.away : form.home)}, has kept the first inning scoreless in ${held} of his last 10 starts.`,
      context: `an average starter does it about 7 times in 10 · he faces ${clubOf(opp)}`,
      lean: leanOf(rate, LEAGUE.pitcherScoreless, false, pick),
      weight: 90,
    });
  }

  /* ---------------- how the series has gone ---------------- */
  if (h2h.series.length >= 2) {
    const s = h2h.series;
    const yrfi = s.filter((g) => g.total > 0).length;
    out.push({
      id: "series",
      label: "This series",
      figure: ofN(yrfi, s.length),
      sentence: `These two have already played ${s.length} games in this series, and a first-inning run scored in ${yrfi} of them.`,
      context: `${cityOf(away)} at ${cityOf(home)}, this season`,
      lean: leanOf(yrfi / s.length, LEAGUE.yrfi, true, pick),
      weight: 40,
    });
  } else if (h2h.games.length >= 3) {
    const g = h2h.games;
    const yrfi = g.filter((x) => x.total > 0).length;
    out.push({
      id: "h2h",
      label: "Head to head",
      figure: ofN(yrfi, g.length),
      sentence: `${cityOf(away)} and ${cityOf(home)} have met ${g.length} times this season, and a first-inning run scored in ${yrfi} of those games.`,
      context: null,
      lean: leanOf(yrfi / g.length, LEAGUE.yrfi, true, pick),
      weight: 40,
    });
  }

  /* ---------------- let the model reorder, if it can ---------------- */
  if (drivers && drivers.length) {
    const byReason = new Map<string, number>();
    for (const d of drivers) {
      const id = FEATURE_TO_REASON[d.name];
      if (!id) continue;
      byReason.set(id, (byReason.get(id) ?? 0) + Math.abs(d.contribution));
    }
    if (byReason.size) {
      // Scale onto the same range as the editorial weights so a reason the
      // model never used does not silently vanish beneath one it barely did.
      const max = Math.max(...byReason.values());
      for (const r of out) {
        const c = byReason.get(r.id);
        if (c != null && max > 0) r.weight = 100 + (c / max) * 100;
      }
    }
  }

  const rank = (a: Reason, b: Reason) =>
    b.weight - a.weight || a.id.localeCompare(b.id);
  return {
    supports: out.filter((r) => r.lean === "SUPPORTS").sort(rank),
    against: out.filter((r) => r.lean === "AGAINST").sort(rank),
    neutral: out.filter((r) => r.lean === "NEUTRAL").sort(rank),
  };
}

/**
 * A park's standing, said in a way the data can actually support.
 *
 * NOT AN ORDINAL, DELIBERATELY. The first draft printed "2nd most
 * run-friendly of 30 parks" for Colorado. True, and misleading: Arizona
 * sits at 0.4239 and Colorado at 0.4241, a gap of two ten-thousandths.
 * An ordinal claims those two are ranked when they are a dead heat, and
 * the operator would have said it into a camera as though it meant
 * something. Tiers survive that noise, and "one of the five most
 * run-friendly first innings in baseball" is also simply how a person
 * would say it.
 */
export function parkTier(rank: number | null, parks: number): string | null {
  if (rank == null || parks <= 0) return null;
  if (rank <= 5) return "one of the five most run-friendly first innings in baseball";
  if (rank <= 10) return "top ten in baseball for first-inning scoring";
  if (rank > parks - 5) return "one of the five quietest first innings in baseball";
  if (rank > parks - 10) return "bottom ten in baseball for first-inning scoring";
  return "around the middle of the league for first-inning scoring";
}
