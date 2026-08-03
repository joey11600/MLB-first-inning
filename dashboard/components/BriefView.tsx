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
  formatLevel,
  formatReturn,
  EM_DASH,
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

export function BriefView({
  date,
  play,
  reasons,
  form,
  record,
  otherPlays,
}: {
  date: string;
  play: BriefPlay | null;
  reasons: { supports: Reason[]; against: Reason[]; neutral: Reason[] } | null;
  form: GameFiForm | null;
  record: TopPickReport | null;
  otherPlays: { gamePk: string; away: string; home: string; side: string }[];
}) {
  return (
    <main className={styles.page}>
      <header className={styles.head}>
        <Link href="/" className={styles.back}>
          ← slate
        </Link>
        <span className={styles.headTitle}>the brief</span>
        <Link href="/history" className={styles.back}>
          history →
        </Link>
      </header>

      {play == null ? (
        <EmptyBrief date={date} />
      ) : (
        <>
          <PlayHeader play={play} date={date} />
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
          <RecordBlock record={record} />
          {otherPlays.length > 0 && (
            <section className={styles.block}>
              <h2 className={styles.h2}>Also on tonight</h2>
              <ul className={styles.otherList}>
                {otherPlays.map((o) => (
                  <li key={o.gamePk}>
                    <Link href={`/brief?game=${o.gamePk}`} className={styles.otherLink}>
                      <span>
                        {cityOf(o.away)} at {cityOf(o.home)}
                      </span>
                      <span className={styles.otherSide}>{o.side}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </main>
  );
}

/* ------------------------------------------------------------------ */

function EmptyBrief({ date }: { date: string }) {
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

function PlayHeader({ play, date }: { play: BriefPlay; date: string }) {
  const meaning =
    play.side === "YRFI"
      ? "a run scores in the first inning"
      : "no run scores in the first inning";
  return (
    <section className={styles.play}>
      <div className={styles.playTag}>#1 play · {prettyDate(date)}</div>
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

      <dl className={styles.ticket}>
        <div className={styles.ticketRow}>
          <dt>Bet</dt>
          <dd className={styles.ticketSide}>{play.side}</dd>
        </div>
        <div className={styles.ticketRow}>
          <dt>Price</dt>
          <dd>{play.odds != null ? formatAmerican(play.odds) : "not captured"}</dd>
        </div>
        <div className={styles.ticketRow}>
          <dt>Stake</dt>
          <dd>{play.stake != null ? formatLevel(play.stake) : EM_DASH}</dd>
        </div>
        <div className={styles.ticketRow}>
          <dt>Model</dt>
          <dd>{Math.round(play.modelP * 100)}% likely</dd>
        </div>
      </dl>
      <p className={styles.meaning}>
        You win if {meaning}. One unit is 1% of your bankroll, so{" "}
        {play.stake != null ? formatLevel(play.stake) : "the stake"} means{" "}
        {play.stake != null ? `${play.stake.toFixed(2)}%` : "that percent"} of
        whatever you are playing with.
      </p>
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
            <p className={styles.parkFigure}>
              {Math.round((1 - park.nrfiRate) * 100)}%
              <span className={styles.parkFigureSub}>
                of first innings here have a run in them
              </span>
            </p>
            <p className={styles.context}>
              {parkTier(park.rank, park.parks)
                ? `${capitalise(parkTier(park.rank, park.parks)!)}. `
                : ""}
              League average is {Math.round(LEAGUE.yrfi * 100)}%.
              {park.ledger
                ? ` ${park.ledger.yrfi} of the ${park.ledger.games} first innings actually played here this season had a run.`
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
function RecordBlock({ record }: { record: TopPickReport | null }) {
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
      <p className={styles.blurb}>
        Real bets at real captured prices, not a simulation. This is the number
        to quote when somebody asks whether the top play is any good.
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
        <div>
          <span className={styles.recordFigure}>
            {formatFlatUnits(asFlat(record.totals.atPublishedStakes))}
          </span>
          <span className={styles.recordLabel}>
            units profit at the stakes published, {formatReturn(season.roiPerUnit, 1)}{" "}
            per unit risked
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

function formatAmerican(n: number): string {
  return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
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
