/**
 * THE BRIEF — the play, the case, the counter-case, the detail.
 *
 * READ THE ORDER AS A SCRIPT. The operator films top to bottom: what the
 * bet is, why it is on, what argues against it, then the numbers behind
 * both. That is why this is not a card grid. A grid says "these things
 * are equally important and you may enter anywhere", which is right for
 * the board and wrong here, where the whole value is the sequence.
 *
 * SENTENCES ARE THE PRIMARY CONTENT, FIGURES ARE THE HEADING. Every other
 * surface in this app puts the number first because the operator is
 * deciding. Here they are talking, so each reason leads with a short
 * label and its figure, then gives the full sentence underneath at
 * reading size. The sentence is meant to be said verbatim.
 *
 * HUE NEVER CARRIES A MEANING ALONE, which the palette forces: green and
 * red are exactly the pair that collapses for the commonest colour
 * blindness. Every verdict chip prints the WORD "supports" or "against",
 * every figure that could be read as a direction carries a sign or a
 * count, and the two reason blocks are separated structurally with their
 * own headings rather than by tint.
 */
import Link from "next/link";
import type { GameFiForm, Side, TeamFiForm } from "@/lib/first-inning-form";
import { LEAGUE } from "@/lib/first-inning-form";
import { parkTier, type Reason } from "@/lib/pick-reasons";
import { cityOf, clubOf } from "@/lib/team-names";
import type { TopPickReport } from "@/lib/top-pick";
import {
  asFlat,
  formatFlatUnits,
  formatReturn,
  MINUS,
} from "@/lib/units";
import styles from "./BriefView.module.css";

export interface BriefPlay {
  away: string;
  home: string;
  gamePk: string;
  gameTimeEt: string;
  side: Side;
  modelP: number;
  odds: number | null;
  stake: number | null;
  awayPitcher: string | null;
  homePitcher: string | null;
  strength: string;
}

/** Another pick on the same slate, with everything needed to say what it
 *  is WITHOUT opening it — which side, whether it is bet, and whether it
 *  is the #1. */
export interface BriefOtherPlay {
  gamePk: string;
  away: string;
  home: string;
  side: string;
  strength: "STRONG" | "LEAN";
  isTop: boolean;
  gameTimeEt: string;
}

/** Why there is no play on screen. Three different facts, and collapsing
 *  them into one "nothing here" would be a lie in two of the three cases:
 *  an empty slate is the system working, a pass is a deliberate refusal,
 *  and a missing game means the link pointed at another date. */
export interface BriefEmpty {
  kind: "none" | "pass" | "missing";
  away?: string;
  home?: string;
}

export function BriefView({
  date,
  slateDate,
  play,
  isTop,
  reasons,
  form,
  record,
  otherPlays,
  empty,
}: {
  date: string;
  /** Carried onto every in-page link so a brief opened from an old slate
   *  keeps browsing that slate instead of silently jumping to tonight. */
  slateDate?: string;
  play: BriefPlay | null;
  /** This game is the slate's #1 bet. Decided by the shared selector, so
   *  the badge on the board and the headline here cannot disagree. */
  isTop: boolean;
  reasons: { supports: Reason[]; against: Reason[]; neutral: Reason[] } | null;
  form: GameFiForm | null;
  record: TopPickReport | null;
  otherPlays: BriefOtherPlay[];
  empty?: BriefEmpty;
}) {
  const briefHref = (gamePk: string) =>
    `/brief?game=${encodeURIComponent(gamePk)}` +
    (slateDate ? `&date=${encodeURIComponent(slateDate)}` : "");

  return (
    <main className={styles.page}>
      <header className={styles.head}>
        <Link href={slateDate ? `/?date=${encodeURIComponent(slateDate)}` : "/"} className={styles.back}>
          ← slate
        </Link>
        <span className={styles.headTitle}>the brief</span>
        <Link href="/history" className={styles.back}>
          history →
        </Link>
      </header>

      {play == null ? (
        <EmptyBrief date={date} empty={empty} />
      ) : (
        <>
          <PlayHeader play={play} date={date} isTop={isTop} />
          {reasons && (
            <>
              <ReasonBlock
                kind="for"
                title="The case for it"
                blurb="Say these. Ordered by how much each one actually moved the model's number."
                reasons={reasons.supports}
                empty="Nothing in the recent form argues for this side. The model is leaning on factors this page cannot phrase yet."
              />
              <ReasonBlock
                kind="against"
                title="What cuts against it"
                blurb="Answer these before somebody in the comments does. A play can be right with these on the board; it cannot survive pretending they are not."
                reasons={reasons.against}
                empty="Nothing in the recent form argues the other way."
              />
              {reasons.neutral.length > 0 && (
                <ReasonBlock
                  kind="neutral"
                  title="Close to average"
                  blurb="True, but too near the league norm to be worth saying."
                  reasons={reasons.neutral}
                  empty=""
                />
              )}
            </>
          )}
          {form && <Numbers form={form} play={play} />}
          <RecordBlock record={record} isTop={isTop} />
        </>
      )}

      {/* RENDERED IN EVERY STATE, INCLUDING THE DEAD ENDS. A brief opened
          on a passed game or a stale link is exactly when the operator
          most needs the list of games that DO have one. */}
      <OtherPlays plays={otherPlays} href={briefHref} hasPlay={play != null} />
    </main>
  );
}

/* ------------------------------------------------------------------ */

/**
 * The list of the slate's other picks, and the way out of any dead end.
 *
 * EACH ROW SAYS WHETHER IT IS A BET. The list used to be STRONG-only, so
 * "on the list" and "wagered" were the same thing and neither needed
 * saying. Now that leans are briefed too, an unlabelled row would read as
 * a bet — on the one surface that gets read aloud, where the label is the
 * only thing standing between a tracked call and somebody staking it.
 */
function OtherPlays({
  plays,
  href,
  hasPlay,
}: {
  plays: BriefOtherPlay[];
  href: (gamePk: string) => string;
  hasPlay: boolean;
}) {
  if (plays.length === 0) return null;
  const leans = plays.filter((p) => p.strength === "LEAN").length;
  return (
    <section className={styles.block}>
      <h2 className={styles.h2}>
        {hasPlay ? "Also on tonight" : "Tonight's picks"}
        <span className={styles.count}>{plays.length}</span>
      </h2>
      <p className={styles.blurb}>
        Every game the model committed to, each with its own brief. Games
        it passed on are not here — there is no case to make for a game it
        declined to call.
        {leans > 0
          ? ` ${leans === 1 ? "One of these is a lean" : `${leans} of these are leans`}: tracked and graded, never staked.`
          : ""}
      </p>
      <ul className={styles.otherList}>
        {plays.map((o) => (
          <li key={o.gamePk}>
            <Link href={href(o.gamePk)} className={styles.otherLink}>
              <span>
                {cityOf(o.away)} at {cityOf(o.home)}
              </span>
              <span className={styles.otherMeta}>
                <span className={styles.otherSide}>
                  {o.side === "YRFI" ? "run scores" : "no run"}
                </span>
                <span
                  className={styles.otherTag}
                  data-kind={o.isTop ? "top" : o.strength === "LEAN" ? "lean" : "bet"}
                >
                  {o.isTop ? "#1 bet" : o.strength === "LEAN" ? "lean · not bet" : "bet"}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function EmptyBrief({ date, empty }: { date: string; empty?: BriefEmpty }) {
  const kind = empty?.kind ?? "none";

  /* THE MODEL REFUSED THIS GAME, which is a different fact from "there is
     nothing tonight" and has to read as one. Somebody arriving here has
     followed a link to a specific matchup, and the honest answer is that
     there is no case to make, not that the page is broken. */
  if (kind === "pass") {
    const named =
      empty?.away && empty?.home
        ? `${cityOf(empty.away)} at ${cityOf(empty.home)}`
        : null;
    return (
      <section className={styles.empty}>
        <p className={styles.emptyLead}>The model passed on this one.</p>
        <p className={styles.emptyBody}>
          {named ? `${named}: the` : "The"} model looked at this game and
          declined to call it either way, so there is nothing here to argue
          and nothing to film. A brief exists for the plays the system
          committed to — the strong ones it bets and the leans it tracks —
          because those are the ones with a case behind them. Writing one
          for a pass would mean inventing the case.
        </p>
      </section>
    );
  }

  if (kind === "missing") {
    return (
      <section className={styles.empty}>
        <p className={styles.emptyLead}>That game isn&rsquo;t on this slate.</p>
        <p className={styles.emptyBody}>
          Nothing on the {prettyDate(date)} board matches the game in this
          link. Usually it points at a different date — open the slate for
          that day and follow the brief link from the game&rsquo;s own row.
        </p>
      </section>
    );
  }

  return (
    <section className={styles.empty}>
      <p className={styles.emptyLead}>No play tonight.</p>
      <p className={styles.emptyBody}>
        The model found nothing on the {prettyDate(date)} slate confident
        enough to bet. About a third of nights end this way. It is the
        system working, not a fault, and there is nothing to film.
      </p>
    </section>
  );
}

function PlayHeader({
  play,
  date,
  isTop,
}: {
  play: BriefPlay;
  date: string;
  isTop: boolean;
}) {
  const meaning =
    play.side === "YRFI"
      ? "a run scores in the first inning"
      : "no run scores in the first inning";
  /* A LEAN IS NOT A BET, AND THE PAGE SAYS SO THREE TIMES: in the tag
     above the matchup, in the ticket where the stake would be, and in the
     paragraph under it. That is deliberate repetition. This surface gets
     read ALOUD, and a qualifier stated once is a qualifier that falls off
     in the edit. */
  const isLean = play.strength === "LEAN";
  const tag = isTop ? "#1 play" : isLean ? "Lean · not bet" : "Strong play";
  return (
    <section className={styles.play}>
      <div className={styles.playTag} data-kind={isLean ? "lean" : "bet"}>
        {tag} · {prettyDate(date)}
      </div>
      <h1 className={styles.matchup}>
        {cityOf(play.away)} <span className={styles.at}>at</span>{" "}
        {cityOf(play.home)}
      </h1>
      <p className={styles.matchupSub}>
        {play.gameTimeEt || "time TBD"}
        {play.awayPitcher && play.homePitcher ? (
          <>
            {" · "}
            {play.awayPitcher} vs {play.homePitcher}
          </>
        ) : null}
      </p>

      {/* EVERY ROW HAS A SPOKEN FORM (2026-08-03).
          These were machine strings: a bare "YRFI", a bare "7.00u", an
          em dash when no price was captured, and worst, "71% likely"
          which never named WHAT was likely. On an NRFI night that figure
          is the chance NO run scores, while the ballpark block on the
          same page prints a percentage meaning the opposite -- and both
          can read "58%". The acronym survives as a trailing tag because
          it is what the DraftKings ticket says; the sentence in front of
          it is what gets read out. */}
      <dl className={styles.ticket}>
        <div className={styles.ticketRow}>
          <dt>Bet</dt>
          <dd className={styles.ticketSide}>
            {meaning}
            <span className={styles.sideTag}>{play.side}</span>
          </dd>
        </div>
        <div className={styles.ticketRow}>
          <dt>Price</dt>
          <dd>
            {play.odds != null
              ? formatAmerican(play.odds)
              : "no price captured"}
          </dd>
        </div>
        <div className={styles.ticketRow}>
          <dt>Stake</dt>
          <dd className={isLean ? styles.ticketNote : undefined}>
            {isLean
              ? "nothing — this one is not bet"
              : play.stake != null
                ? `${play.stake.toFixed(2)} units`
                : "no stake, no price captured"}
          </dd>
        </div>
        <div className={styles.ticketRow}>
          <dt>Model</dt>
          <dd>
            {Math.round(play.modelP * 100)}% chance {meaning}
          </dd>
        </div>
      </dl>
      {isLean ? (
        <p className={styles.leanNote}>
          This is a lean, not a bet. The model has a side and the system
          records it and grades it, so the call still counts toward how the
          model is measured — but no money goes down on a lean. Only the
          strong plays get staked. Nothing on this page is telling you to
          bet this game.
        </p>
      ) : (
        <p className={styles.meaning}>
          One unit is 1% of your bankroll, so{" "}
          {play.stake != null
            ? `${play.stake.toFixed(2)} units is ${play.stake.toFixed(2)}%`
            : "the stake is that percent"}{" "}
          of whatever you are playing with, whether that is a thousand dollars
          or ten thousand.
        </p>
      )}
    </section>
  );
}

function ReasonBlock({
  kind,
  title,
  blurb,
  reasons,
  empty,
}: {
  kind: "for" | "against" | "neutral";
  title: string;
  blurb: string;
  reasons: Reason[];
  empty: string;
}) {
  if (reasons.length === 0 && !empty) return null;
  return (
    <section className={`${styles.block} ${styles[`block_${kind}`]}`}>
      <h2 className={styles.h2}>
        {title}
        <span className={styles.count}>{reasons.length}</span>
      </h2>
      <p className={styles.blurb}>{blurb}</p>
      {reasons.length === 0 ? (
        <p className={styles.none}>{empty}</p>
      ) : (
        <ol className={styles.reasons}>
          {reasons.map((r) => (
            <li key={r.id} className={styles.reason}>
              <div className={styles.reasonTop}>
                <span className={styles.reasonLabel}>{r.label}</span>
                <span className={styles.reasonFigure}>{r.figure}</span>
              </div>
              <p className={styles.sentence}>{r.sentence}</p>
              {r.context && <p className={styles.context}>{r.context}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** The four stat groups, in the order they get talked about. */
function Numbers({ form, play }: { form: GameFiForm; play: BriefPlay }) {
  const { park, h2h } = form;
  return (
    <section className={styles.block}>
      <h2 className={styles.h2}>The numbers</h2>
      <p className={styles.blurb}>
        Every figure here is from this system&rsquo;s own record of all{" "}
        {LEAGUE.measuredOver} first innings played this season, so it is
        checkable. The league columns are what an average team or an average
        starter does, which is what makes any of it mean something.
      </p>

      <TeamStrip team={form.awayTeam} label={cityOf(form.away)} />
      <TeamStrip team={form.homeTeam} label={cityOf(form.home)} />

      <div className={styles.statPair}>
        <PitcherBlock
          name={play.awayPitcher}
          form={form.awayPitcher}
          facing={clubOf(form.home)}
        />
        <PitcherBlock
          name={play.homePitcher}
          form={form.homePitcher}
          facing={clubOf(form.away)}
        />
      </div>

      <div className={styles.parkBlock}>
        <h3 className={styles.h3}>The ballpark</h3>
        {park.nrfiRate != null ? (
          <>
            {/* BOTH FIGURES NAME THEIR BASIS. The headline is the model's
                park factor, shrunk over last season and this one; the
                second is the raw count from this season alone. They can
                sit fifteen points apart (Colorado: 58 and 73), and
                printing them unlabelled twenty lines apart is how the
                operator ends up contradicting himself on camera. */}
            <p className={styles.parkFigure}>
              {Math.round((1 - park.nrfiRate) * 100)}%
              <span className={styles.parkFigureSub}>
                of first innings here have a run, taking last season and this
                one together
              </span>
            </p>
            <p className={styles.context}>
              {parkTier(park.rank, park.parks)
                ? `${capitalise(parkTier(park.rank, park.parks)!)}. `
                : ""}
              League average is {Math.round(LEAGUE.yrfi * 100)}%.
              {park.ledger && park.ledger.games > 0
                ? ` This season alone it is ${park.ledger.yrfi} of ${park.ledger.games}, or ${Math.round((park.ledger.yrfi / park.ledger.games) * 100)}% — a smaller sample than the figure above, which is why the model shrinks it toward the league.`
                : ""}
            </p>
          </>
        ) : (
          <p className={styles.none}>
            No park factor on file for {cityOf(park.team)}.
          </p>
        )}
      </div>

      <div className={styles.parkBlock}>
        <h3 className={styles.h3}>
          {h2h.series.length >= 2 ? "This series so far" : "Head to head"}
        </h3>
        {h2h.games.length === 0 ? (
          <p className={styles.none}>
            {cityOf(form.away)} and {cityOf(form.home)} have not met yet this
            season, so there is no head-to-head first-inning history to read.
          </p>
        ) : (
          <>
            <table className={styles.h2hTable}>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>{cityOf(form.away)}</th>
                  <th>{cityOf(form.home)}</th>
                  <th>First inning</th>
                </tr>
              </thead>
              <tbody>
                {(h2h.series.length >= 2 ? h2h.series : h2h.games)
                  .slice(-6)
                  .map((g) => (
                    <tr key={g.date}>
                      <td>{shortDate(g.date)}</td>
                      <td>{g.scored}</td>
                      <td>{g.allowed}</td>
                      <td className={g.total > 0 ? styles.wasYrfi : styles.wasNrfi}>
                        {g.total > 0 ? `${g.total} run${g.total === 1 ? "" : "s"}` : "no runs"}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <p className={styles.context}>
              Runs scored in the first inning only, not the final score.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Ten games, most recent last, each cell showing runs scored in the 1st.
 *
 * The digit IS the encoding. A row of coloured squares would need a
 * legend and would fail for a red-green colour-blind reader; a row of
 * numbers is self-describing, and a zero reads as a zero to everyone.
 * Brightness separates scored from scoreless as a second channel.
 */
function TeamStrip({ team, label }: { team: TeamFiForm; label: string }) {
  const l = team.last10;
  if (l.games === 0) {
    return (
      <div className={styles.teamBlock}>
        <h3 className={styles.h3}>{label}</h3>
        <p className={styles.none}>No graded first innings on file yet.</p>
      </div>
    );
  }
  return (
    <div className={styles.teamBlock}>
      <h3 className={styles.h3}>{label} · first-inning form</h3>
      <p className={styles.teamLead}>
        Scored in the first in{" "}
        <strong>
          {l.scoredIn} of their last {l.games}
        </strong>
        . A run scored either way in{" "}
        <strong>
          {l.yrfiIn} of {l.games}
        </strong>
        .
      </p>
      <ol className={styles.strip} aria-label={`${team.team} last ${l.games} first innings`}>
        {team.recent.map((g) => (
          <li
            key={`${g.date}-${g.opponent}`}
            className={g.scored > 0 ? styles.cellOn : styles.cellOff}
            title={`${g.date} ${g.home ? "vs" : "at"} ${g.opponent}: scored ${g.scored}, allowed ${g.allowed}`}
          >
            <span className={styles.cellRuns}>{g.scored}</span>
            <span className={styles.cellOpp}>
              {g.home ? "" : "@"}
              {g.opponent}
            </span>
          </li>
        ))}
      </ol>
      <p className={styles.context}>
        Runs this team scored in the first inning, oldest first. League average
        is about 3 in 10. Season: scored in {team.season.scoredIn} of{" "}
        {team.season.games}.
        {team.scorelessStreak >= 3
          ? ` Currently ${team.scorelessStreak} straight without one.`
          : ""}
      </p>
    </div>
  );
}

function PitcherBlock({
  name,
  form,
  facing,
}: {
  name: string | null;
  form: GameFiForm["awayPitcher"];
  facing: string;
}) {
  if (!form || form.modelLast10 == null) {
    return (
      <div className={styles.pitcherBlock}>
        <h3 className={styles.h3}>{name || "Starter TBD"}</h3>
        <p className={styles.none}>
          No last-10 record on file. Usually means a rookie or a starter just
          off the injured list.
        </p>
      </div>
    );
  }
  const held = Math.round(form.modelLast10 * 10);
  return (
    <div className={styles.pitcherBlock}>
      <h3 className={styles.h3}>{name || form.name}</h3>
      <p className={styles.pitcherFigure}>
        {held} <span className={styles.pitcherOf}>of 10</span>
      </p>
      <p className={styles.pitcherSub}>
        last starts with a scoreless first, facing {facing}
      </p>
      <p className={styles.context}>
        An average starter manages about 7 of 10.
        {form.modelLast5 != null
          ? ` Narrowed to his last 5 starts: ${Math.round(form.modelLast5 * 5)} of 5.`
          : ""}
        {form.season.starts > 0
          ? ` Across the ${form.season.starts} starts this system has graded him, ${form.season.scoreless} were scoreless.`
          : ""}
      </p>
    </div>
  );
}

/**
 * How the #1 play has actually done.
 *
 * A RATIO, NEVER A TOTAL. Units from different dates are amounts in
 * different currencies once the bank has moved, so `lib/units` refuses to
 * print a summed unit figure at compile time. Return per unit staked is
 * the scale-free answer and the one a follower on any bankroll actually
 * experienced. See the file header of lib/units.ts.
 */
function RecordBlock({
  record,
  isTop,
}: {
  record: TopPickReport | null;
  isTop: boolean;
}) {
  if (!record || record.windows.length === 0) {
    return (
      <section className={styles.block}>
        <h2 className={styles.h2}>The #1 play&rsquo;s record</h2>
        <p className={styles.none}>
          No settled #1 plays with a captured price yet this season.
        </p>
      </section>
    );
  }
  const season = record.windows[0];
  const last10 = record.recent.slice(0, 10);
  const w10 = last10.filter((b) => b.win).length;
  return (
    <section className={styles.block}>
      <h2 className={styles.h2}>The #1 play&rsquo;s record</h2>
      {/* THE TITLE IS A CLAIM, AND ON A NON-#1 BRIEF IT IS A CLAIM ABOUT
          A DIFFERENT GAME. This block tracks whichever play was #1 on each
          night. On the #1's own page that reads as "this pick's record",
          which is right. On any other brief the same words would be read
          aloud as the record of THAT game, which is false — so the page
          says whose record it is rather than relying on the reader to
          remember. */}
      <p className={styles.blurb}>
        Real bets at real captured prices, not a simulation. This is the number
        to quote when somebody asks whether the top play is any good.
        {!isTop
          ? " It follows whichever game was the #1 play on each night, so it is the system's headline record — not a record for the game above."
          : ""}
      </p>
      <div className={styles.recordGrid}>
        <div>
          <span className={styles.recordFigure}>
            {season.wins}&minus;{season.losses}
          </span>
          <span className={styles.recordLabel}>
            season, since {shortDate(season.from)}
          </span>
        </div>
        <div>
          <span className={styles.recordFigure}>
            {w10}&minus;{last10.length - w10}
          </span>
          <span className={styles.recordLabel}>last {last10.length}</span>
        </div>
        <div>
          <span className={styles.recordFigure}>
            {Math.round(season.hitRate * 1000) / 10}%
          </span>
          <span className={styles.recordLabel}>
            hit rate, needs {Math.round(season.breakEven * 1000) / 10}%
          </span>
        </div>
        {/* THE ONLY MONEY FIGURE ON THE FILMED PAGE, and until 2026-08-03
            the only one with no `data-money`. `.recordFigure` sets no
            colour, so it inherited `--foreground` #00FF41 -- byte-identical
            to `--gain`. A losing season would have printed in the colour
            this codebase reserves for money UP, on the surface whose whole
            rule is that a wrong sentence is worse than a missing one. */}
        {/* THE FLAT FIGURE, NOT THE KELLY ONE, and that is deliberate.
            /history carries the quarter-Kelly simulation with a dashed
            rule and the word "simulated" on it. This surface gets read
            ALOUD, where a caveat is the first thing that falls off, so
            it prints the number that needs no caveat: what the edge is
            worth at a flat 1 unit a night. */}
        <div>
          <span
            className={styles.recordFigure}
            data-money={moneyTone(record.totals.atFlat1u)}
          >
            {formatFlatUnits(asFlat(record.totals.atFlat1u))}
          </span>
          <span className={styles.recordLabel}>
            at a flat 1 unit a night
          </span>
          <span className={styles.recordSub}>
            {formatReturn(season.roiPerUnit, 1)} per unit at today&rsquo;s
            staking
          </span>
        </div>
      </div>
      <ol className={styles.last10} aria-label="last ten #1 plays, most recent first">
        {last10.map((b) => (
          <li
            key={`${b.date}-${b.game}`}
            className={b.win ? styles.wOn : styles.wOff}
            title={`${b.date} ${b.game} ${b.side} ${b.win ? "won" : "lost"}`}
          >
            {b.win ? "W" : "L"}
          </li>
        ))}
      </ol>
      <p className={styles.context}>
        Most recent on the left. A 10-bet run is far too short to prove
        anything either way; the season line is the one that carries weight,
        and even that is {season.bets} bets.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ */

/** U+2212 MINUS, not a hyphen, so a negative price reads as a number.
 *  Matches what lib/units.ts does for every other signed figure. */
function formatAmerican(n: number): string {
  const v = Math.round(n);
  return v > 0 ? `+${v}` : `${MINUS}${Math.abs(v)}`;
}

/** The sign bucket `globals.css` paints via [data-money]. Local copy:
 *  TopPickHistory has the same three lines and does not export them. */
function moneyTone(x: number): "up" | "down" | "flat" {
  if (x > 0.0005) return "up";
  if (x < -0.0005) return "down";
  return "flat";
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function prettyDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

function shortDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
