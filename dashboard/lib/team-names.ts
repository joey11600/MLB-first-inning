/**
 * TEAM NAMES, because "TB" is not a word.
 *
 * Every other surface in this app is read silently by one person who
 * knows the abbreviations cold, so "TB at COL" is the right density
 * there and stays. The brief is read ALOUD to people who mostly do not,
 * and an abbreviation forces the reader to do the expansion mid-sentence.
 *
 * Two forms because English needs both:
 *   `city`  — "Tampa Bay", for the subject of a sentence about the club.
 *   `club`  — "the Rays", for the second reference and for anything that
 *             wants a definite article.
 *
 * Falls back to the abbreviation itself for anything unrecognised
 * (relocations, a new franchise, a typo in a board CSV) so an unknown
 * code degrades to today's behaviour rather than to an empty string.
 */
const TEAMS: Record<string, { city: string; club: string }> = {
  ARI: { city: "Arizona", club: "the Diamondbacks" },
  ATL: { city: "Atlanta", club: "the Braves" },
  BAL: { city: "Baltimore", club: "the Orioles" },
  BOS: { city: "Boston", club: "the Red Sox" },
  CHC: { city: "Chicago", club: "the Cubs" },
  CWS: { city: "Chicago", club: "the White Sox" },
  CIN: { city: "Cincinnati", club: "the Reds" },
  CLE: { city: "Cleveland", club: "the Guardians" },
  COL: { city: "Colorado", club: "the Rockies" },
  DET: { city: "Detroit", club: "the Tigers" },
  HOU: { city: "Houston", club: "the Astros" },
  KC: { city: "Kansas City", club: "the Royals" },
  LAA: { city: "the Angels", club: "the Angels" },
  LAD: { city: "the Dodgers", club: "the Dodgers" },
  MIA: { city: "Miami", club: "the Marlins" },
  MIL: { city: "Milwaukee", club: "the Brewers" },
  MIN: { city: "Minnesota", club: "the Twins" },
  NYM: { city: "the Mets", club: "the Mets" },
  NYY: { city: "the Yankees", club: "the Yankees" },
  OAK: { city: "the Athletics", club: "the Athletics" },
  PHI: { city: "Philadelphia", club: "the Phillies" },
  PIT: { city: "Pittsburgh", club: "the Pirates" },
  SD: { city: "San Diego", club: "the Padres" },
  SEA: { city: "Seattle", club: "the Mariners" },
  SF: { city: "San Francisco", club: "the Giants" },
  STL: { city: "St. Louis", club: "the Cardinals" },
  TB: { city: "Tampa Bay", club: "the Rays" },
  TEX: { city: "Texas", club: "the Rangers" },
  TOR: { city: "Toronto", club: "the Blue Jays" },
  WSH: { city: "Washington", club: "the Nationals" },
};

/**
 * "Tampa Bay". The SUBJECT of a sentence, or the object of "for" / "at".
 *
 * NEVER PUT THIS AFTER "IN". For five clubs the `city` field above is a
 * club name, because the city is shared (CHC/CWS, LAA/LAD, NYM/NYY) or
 * would not identify the team: LAA, LAD, NYM, NYY and OAK all return
 * "the Angels"-style strings. So "in ${cityOf(home)}" produces "in the
 * Yankees", which shipped on the brief's ballpark sentence and was found
 * on 2026-08-03. For anything locative use `clubOf` with a phrase that
 * takes a club: "at home for the Yankees". See the park block in
 * lib/pick-reasons.ts for the full reasoning.
 */
export function cityOf(abbr: string): string {
  return TEAMS[abbr?.trim().toUpperCase()]?.city ?? abbr;
}

/** "the Rays". Takes its own article, so never write "the {clubOf(x)}". */
export function clubOf(abbr: string): string {
  return TEAMS[abbr?.trim().toUpperCase()]?.club ?? abbr;
}

/** "Tampa Bay Rays"-ish, for a heading that wants the whole thing. */
export function fullNameOf(abbr: string): string {
  const t = TEAMS[abbr?.trim().toUpperCase()];
  if (!t) return abbr;
  const club = t.club.replace(/^the /, "");
  return t.city === club ? t.city : `${t.city} ${club}`;
}
