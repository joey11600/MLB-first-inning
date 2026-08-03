/**
 * FIRST-INNING FORM — the numbers a person can say out loud.
 *
 * WHY THIS EXISTS. Every other figure on this dashboard answers "what do
 * I bet and how much". This file answers a different question that the
 * operator started asking on 2026-08-03: *why is this the play*, phrased
 * so an audience who cannot see the screen understands it. "Coors Field,
 * where a run scores in the first more often than any park in baseball"
 * is a sentence. "fi_park_nrfi_rate z = −1.476" is not.
 *
 * EVERY NUMBER HERE IS DERIVED FROM THE LEDGER, NOT FETCHED. The board
 * logs EVERY game on every slate, not just the ones it bets, and it
 * records each game's first-inning line (`fi_away_runs`, `fi_home_runs`)
 * at grade time. That means the ledger already contains a complete
 * first-inning game log for all 30 teams. No new scraping, no new cron,
 * no new failure mode. Checked 2026-08-03: 119 slate dates from 04-01 to
 * 07-31 with exactly three calendar gaps — 07-13, 07-14, 07-15 — which
 * are the All-Star break, when nobody played.
 *
 * THE ONE PLACE THE LEDGER IS NOT AUTHORITATIVE, and it is handled
 * rather than hidden: a STARTING PITCHER'S last-10 record. Reconstructing
 * it from the ledger agrees with the value the model actually used on
 * 540 of 562 checks; the 22 misses are all one game apart, because the
 * ledger stores the PROBABLE starter named at predict time and a late
 * scratch makes that name wrong for that row. So the headline pitcher
 * figure is the STORED model value (`*_p_last10_pitcher_nrfi`, fetched
 * from StatsAPI), and the ledger reconstruction is used only for the
 * game-by-game strip, which is labelled as covering this board's slates.
 * Two sources, each used for what it is actually good for, and the
 * disagreement disclosed rather than averaged away.
 *
 * WHY EVERY STAT CARRIES A DIRECTION. The operator's first instinct for
 * tonight's card was "the Rays haven't scored in the first all series,
 * so they're due" — which is the gambler's fallacy, and worse, it points
 * the opposite way from the bet the model actually made. A stat with no
 * declared direction is a trap when you are reading it into a camera.
 * So `leanOf()` scores every figure against the LEAGUE BASELINE and
 * labels it SUPPORTS / AGAINST / NEUTRAL relative to the side actually
 * being bet. The figures that cut AGAINST the pick are not buried: they
 * are the most useful thing on the card, because they are what the
 * operator has to answer before somebody in the comments does.
 */
import { loadLedgerRows } from "./roi";

/* ------------------------------------------------------------------ *
 * League baselines
 *
 * Measured over the 1,575 graded first innings in the 2026 ledger on
 * 2026-08-03. They are the reference that turns a raw count into a
 * judgement: "scored in 2 of their last 10" only means something once
 * you know the league figure is about 3 in 10.
 *
 * Held as constants rather than recomputed per request because they
 * move at the third decimal over a month and every card on a slate
 * would otherwise recompute the same sum over the whole season.
 * `LEAGUE.measuredOver` is printed in the UI so the sample is visible.
 * ------------------------------------------------------------------ */
export const LEAGUE = {
  /** A run scores in the 1st, either team. */
  yrfi: 0.5105,
  /** One given team scores in the 1st. */
  teamScores: 0.2978,
  /** One given starter holds a scoreless 1st. */
  pitcherScoreless: 0.7022,
  measuredOver: 1575,
  season: 2026,
} as const;

export type Side = "YRFI" | "NRFI";
export type Lean = "SUPPORTS" | "AGAINST" | "NEUTRAL";

/**
 * Which way a rate points, and whether that helps the bet.
 *
 * `rate` is the observed figure, `baseline` the league norm, and
 * `towardYrfiWhenHigh` says which direction a HIGH reading argues for.
 * A team scoring often argues for YRFI; a pitcher holding scoreless
 * innings often argues for NRFI, so that one passes `false`.
 *
 * The dead band is a tenth of the baseline, roughly one game in ten.
 * Without it a 30.1%-vs-29.8% difference would render as a green
 * SUPPORTS chip, which is noise dressed as evidence — the exact thing
 * this dashboard has been cleared of elsewhere.
 */
export function leanOf(
  rate: number | null,
  baseline: number,
  towardYrfiWhenHigh: boolean,
  pick: Side,
): Lean {
  if (rate == null || !Number.isFinite(rate)) return "NEUTRAL";
  const band = baseline * 0.1;
  const delta = rate - baseline;
  if (Math.abs(delta) < band) return "NEUTRAL";
  const arguesYrfi = towardYrfiWhenHigh ? delta > 0 : delta < 0;
  const argued: Side = arguesYrfi ? "YRFI" : "NRFI";
  return argued === pick ? "SUPPORTS" : "AGAINST";
}

/** One team's first inning in one game, from that team's point of view. */
export interface FiGame {
  date: string;
  /** Opponent abbreviation. */
  opponent: string;
  home: boolean;
  /** Runs this team scored in the 1st. */
  scored: number;
  /** Runs this team allowed in the 1st. */
  allowed: number;
  /** Both halves. Zero means the game was an NRFI. */
  total: number;
}

export interface TeamFiForm {
  team: string;
  /** Chronological, oldest first. Most recent 10 at the end. */
  recent: FiGame[];
  last10: {
    games: number;
    /** Games in which this team scored in the 1st. */
    scoredIn: number;
    /** Games in which a run scored either way. */
    yrfiIn: number;
    runsScored: number;
    runsAllowed: number;
  };
  season: { games: number; scoredIn: number; yrfiIn: number };
  /** Most recent consecutive games without scoring in the 1st. */
  scorelessStreak: number;
  /** Most recent consecutive games with no run either way. */
  nrfiStreak: number;
}

export interface PitcherFiForm {
  name: string;
  id: string;
  /** The rate the MODEL used, from StatsAPI. Authoritative. 0..1. */
  modelLast10: number | null;
  modelLast5: number | null;
  /** Ledger-derived starts, chronological. Strip only, see file header. */
  recent: { date: string; opponent: string; allowed: number }[];
  season: { starts: number; scoreless: number };
}

export interface ParkFiForm {
  /** Home team, which is to say the park. */
  team: string;
  /** p(no run in the 1st) here. LOW means a run-friendly park. */
  nrfiRate: number | null;
  /** 1 = most run-friendly park in the league. */
  rank: number | null;
  parks: number;
  /** This season's first innings actually played here, from the ledger. */
  ledger: { games: number; yrfi: number } | null;
}

export interface HeadToHead {
  /** These two teams' first innings this season, from the away side. */
  games: FiGame[];
  /** The tail of `games` that belongs to the most recent series. */
  series: FiGame[];
}

export interface GameFiForm {
  date: string;
  away: string;
  home: string;
  awayTeam: TeamFiForm;
  homeTeam: TeamFiForm;
  awayPitcher: PitcherFiForm | null;
  homePitcher: PitcherFiForm | null;
  park: ParkFiForm;
  h2h: HeadToHead;
}

const num = (v: string | undefined): number | null => {
  if (v == null) return null;
  const s = v.trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

/** Trailing run of games satisfying `hit`, counted back from the end. */
function trailingStreak(games: FiGame[], hit: (g: FiGame) => boolean): number {
  let n = 0;
  for (let i = games.length - 1; i >= 0; i--) {
    if (!hit(games[i])) break;
    n++;
  }
  return n;
}

function summarise(games: FiGame[]): TeamFiForm["last10"] {
  return {
    games: games.length,
    scoredIn: games.filter((g) => g.scored > 0).length,
    yrfiIn: games.filter((g) => g.total > 0).length,
    runsScored: games.reduce((a, g) => a + g.scored, 0),
    runsAllowed: games.reduce((a, g) => a + g.allowed, 0),
  };
}

/**
 * Split a team's game list into series by opponent run.
 *
 * A "series" is consecutive games against the same opponent — which is
 * how baseball is actually scheduled and how the operator talks about it
 * ("they didn't score in the first all series"). Returns the most recent
 * run only.
 */
function currentSeries(games: FiGame[]): FiGame[] {
  if (games.length === 0) return [];
  const last = games[games.length - 1];
  const out: FiGame[] = [];
  for (let i = games.length - 1; i >= 0; i--) {
    if (games[i].opponent !== last.opponent) break;
    out.unshift(games[i]);
  }
  return out;
}

/**
 * The whole first-inning picture for one matchup.
 *
 * `beforeDate` excludes the game being previewed and anything after it,
 * so a card rendered for a past date shows what was knowable THEN. Without
 * it, opening an old slate would show form that includes the result of
 * the very game on screen, which is the same leakage the model's own
 * training data was audited for on 2026-08-02.
 */
export async function loadGameFiForm(opts: {
  season: number;
  date: string;
  away: string;
  home: string;
  awayPitcherId?: string | null;
  homePitcherId?: string | null;
  awayPitcherName?: string | null;
  homePitcherName?: string | null;
  parkFactors?: Record<string, number> | null;
}): Promise<GameFiForm | null> {
  const rows = await loadLedgerRows(opts.season);
  if (!rows) return null;

  const teamLog = new Map<string, FiGame[]>();
  const pitcherLog = new Map<string, { date: string; opponent: string; allowed: number }[]>();
  const parkLog = new Map<string, { games: number; yrfi: number }>();
  /** Latest stored model rates per pitcher, which is what the model used. */
  const pitcherModel = new Map<string, { last10: number | null; last5: number | null; name: string }>();

  const sorted = [...rows].sort((a, b) => {
    const d = (a.date || "").localeCompare(b.date || "");
    return d !== 0 ? d : (a.game_pk || "").localeCompare(b.game_pk || "");
  });

  for (const r of sorted) {
    const date = (r.date || "").trim();
    if (!date || date >= opts.date) continue; // strictly before, see doc comment
    const away = (r.away_team || "").trim();
    const home = (r.home_team || "").trim();
    if (!away || !home) continue;

    // The model's own pitcher rates, kept whether or not the game graded:
    // they are a property of the pitcher at predict time, not of a result.
    for (const [idKey, nameKey, l10, l5] of [
      ["home_pitcher_id", "home_pitcher", "home_p_last10_pitcher_nrfi", "home_p_last5_pitcher_nrfi"],
      ["away_pitcher_id", "away_pitcher", "away_p_last10_pitcher_nrfi", "away_p_last5_pitcher_nrfi"],
    ] as const) {
      const pid = (r[idKey] || "").trim();
      if (!pid) continue;
      const v10 = num(r[l10]);
      const v5 = num(r[l5]);
      if (v10 == null && v5 == null) continue;
      pitcherModel.set(pid, { last10: v10, last5: v5, name: (r[nameKey] || "").trim() });
    }

    const ar = num(r.fi_away_runs);
    const hr = num(r.fi_home_runs);
    if (ar == null || hr == null) continue; // ungraded: no first inning yet

    const total = ar + hr;
    const push = (k: string, g: FiGame) => {
      const l = teamLog.get(k);
      if (l) l.push(g);
      else teamLog.set(k, [g]);
    };
    push(away, { date, opponent: home, home: false, scored: ar, allowed: hr, total });
    push(home, { date, opponent: away, home: true, scored: hr, allowed: ar, total });

    // A starter faces the OTHER team's half: the home starter throws the
    // top of the 1st, the away starter the bottom. Getting this backwards
    // silently inverts every pitcher figure on the card.
    const hp = (r.home_pitcher_id || "").trim();
    const ap = (r.away_pitcher_id || "").trim();
    const pp = (k: string, e: { date: string; opponent: string; allowed: number }) => {
      const l = pitcherLog.get(k);
      if (l) l.push(e);
      else pitcherLog.set(k, [e]);
    };
    if (hp) pp(hp, { date, opponent: away, allowed: ar });
    if (ap) pp(ap, { date, opponent: home, allowed: hr });

    const pk = parkLog.get(home) || { games: 0, yrfi: 0 };
    pk.games += 1;
    if (total > 0) pk.yrfi += 1;
    parkLog.set(home, pk);
  }

  const teamForm = (team: string): TeamFiForm => {
    const all = teamLog.get(team) || [];
    const last10 = all.slice(-10);
    return {
      team,
      recent: last10,
      last10: summarise(last10),
      season: {
        games: all.length,
        scoredIn: all.filter((g) => g.scored > 0).length,
        yrfiIn: all.filter((g) => g.total > 0).length,
      },
      scorelessStreak: trailingStreak(all, (g) => g.scored === 0),
      nrfiStreak: trailingStreak(all, (g) => g.total === 0),
    };
  };

  const pitcherForm = (
    id: string | null | undefined,
    name: string | null | undefined,
  ): PitcherFiForm | null => {
    const pid = (id || "").trim();
    if (!pid) return null;
    const starts = pitcherLog.get(pid) || [];
    const model = pitcherModel.get(pid);
    return {
      id: pid,
      name: (name || model?.name || "").trim() || "Starter TBD",
      modelLast10: model?.last10 ?? null,
      modelLast5: model?.last5 ?? null,
      recent: starts.slice(-10),
      season: {
        starts: starts.length,
        scoreless: starts.filter((s) => s.allowed === 0).length,
      },
    };
  };

  // Park rank: 1 is the MOST run-friendly, i.e. the LOWEST p(no run).
  let rank: number | null = null;
  let parks = 0;
  const pf = opts.parkFactors || null;
  const parkRate = pf ? (pf[opts.home] ?? null) : null;
  if (pf) {
    const vals = Object.values(pf).filter((v): v is number => Number.isFinite(v));
    parks = vals.length;
    if (parkRate != null) rank = vals.filter((v) => v < parkRate).length + 1;
  }

  const awayGames = teamLog.get(opts.away) || [];
  const h2hGames = awayGames.filter((g) => g.opponent === opts.home);

  return {
    date: opts.date,
    away: opts.away,
    home: opts.home,
    awayTeam: teamForm(opts.away),
    homeTeam: teamForm(opts.home),
    awayPitcher: pitcherForm(opts.awayPitcherId, opts.awayPitcherName),
    homePitcher: pitcherForm(opts.homePitcherId, opts.homePitcherName),
    park: {
      team: opts.home,
      nrfiRate: parkRate,
      rank,
      parks,
      ledger: parkLog.get(opts.home) || null,
    },
    h2h: { games: h2hGames, series: currentSeries(h2hGames) },
  };
}
