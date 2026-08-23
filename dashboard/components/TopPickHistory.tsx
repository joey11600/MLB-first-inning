/**
 * A SETTLED-BET SECTION, MEASURED AT THE PUBLISHED STAKING RULE.
 *
 * Used twice on /history: once for the #1 play of each night, once for
 * the whole system. Same rules, same staking, same shape, so the two can
 * be read against each other without holding two definitions in mind.
 *
 * NOTHING COMPOUNDS. Operator, 2026-08-03: *"compounding is up to the
 * bettor, not the system."* The system emits a unit COUNT; what a unit
 * is worth after a winning week is the follower's business. Every bank
 * level, peak and drawdown was removed rather than hidden.
 *
 * NOT LABELLED "SIMULATED" EITHER, and that was a real correction. The
 * games, prices and results are all real; only the STAKE comes from the
 * rule rather than from history, which is equally true of a flat-1u
 * figure nobody would call simulated. Quarter-Kelly is what the system
 * tells you to bet, so it is what the system's record is measured at.
 * The operator's realized figure still prints beside it, because they
 * staked a flat unit until 2026-07-27 and that gap is a fact about
 * EXECUTION — not a correction to the number next to it.
 */
import type { TopPickReport, TopPickSlice } from "@/lib/top-pick";
import {
  asFlat,
  formatUnits,
  formatBankGrowth,
  formatFlatUnits,
  formatLevel,
  formatReturn,
  returnAsUnits,
  MINUS,
} from "@/lib/units";
import { cityOf } from "@/lib/team-names";
import styles from "./TopPickHistory.module.css";

function tone(x: number): "up" | "down" | "flat" {
  if (x > 0.0005) return "up";
  if (x < -0.0005) return "down";
  return "flat";
}

function monthName(key: string): string {
  const [y, m] = key.split("-").map(Number);
  if (!y || !m) return key;
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", {
    month: "long",
    timeZone: "UTC",
  });
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function TopPickHistory({
  report,
  title = "The #1 play, under today’s rules",
  // Says WHICH population, not just which arithmetic. "The top play of
  // each night" would be true of the maths and misleading about the set:
  // NRFI is excluded outright (switched off 2026-06-07 for losing), so on
  // 15 of 92 nights the overall top play was an NRFI pick and the best
  // YRFI play stands in its place. That is a defensible modelled record --
  // it is what today's rules would have produced -- but a reader is
  // entitled to know it is not a transcript of what was alerted.
  what = "The top YRFI play of each night — NRFI excluded, switched off 2026-06-07",
  id = "topPickHistTitle",
  everyLabel = "Every #1 play, most recent first",
  splitAgainst,
}: {
  report: TopPickReport | null;
  title?: string;
  what?: string;
  id?: string;
  everyLabel?: string;
  /** The #1-play report, when THIS section is the whole system. Used to
   *  show the split, because the two figures look contradictory side by
   *  side: the top play out-earns the whole slate it belongs to. It is
   *  not a contradiction -- everything below #1 loses -- but nobody
   *  should have to do that subtraction themselves. */
  splitAgainst?: TopPickReport | null;
}) {
  if (!report || report.all.length === 0) return null;
  const { last10, byMonth, all, totals, noEdgeUnderKelly, cumulative, sinceUpdate } = report;
  const season = report.windows[0];
  const nights = new Set(all.map((b) => b.date)).size;
  /* A night's P&L is a single point in time, so this is a real quantity
     and needs no bank behind it (lib/units.ts). */
  const byNight = new Map<string, number>();
  for (const b of all) byNight.set(b.date, (byNight.get(b.date) ?? 0) + b.kellyPnl);
  const worstNight = Math.min(0, ...byNight.values());

  return (
    <section className={styles.wrap} aria-labelledby={id}>
      <h2 className={styles.title} id={id}>
        {title}
      </h2>
      <p className={styles.lead}>
        {what} since May 26, staked at quarter-Kelly &mdash; the rule the
        system actually publishes. One continuous ledger: the model was
        updated on Aug 23 (pooled first-inning pitcher xwOBA, 20 features)
        and its picks simply continue the series from there; nothing before
        that date is rewritten.{" "}
        {all.length} bets over {nights} nights. NRFI is excluded because it
        has been switched off since June 7.
        {noEdgeUnderKelly > 0 && (
          <>
            {" "}
            A further {noEdgeUnderKelly} qualified on every other count but
            are not counted here, because quarter-Kelly finds no edge at the
            price actually paid and so would stake nothing.
          </>
        )}
      </p>

      {/* THE BASIS IS NAMED ABOVE THE FIGURE. Three sums, one rule
          each, and the first is the system's own. */}
      <h3 className={styles.h3}>Units profit</h3>
      <div className={`${styles.figures} ${styles.figuresThree}`}>
        <div className={styles.fig}>
          <span className={styles.figBasis}>At quarter-Kelly</span>
          <span className={styles.figValue} data-money={tone(totals.atKelly)}>
            {formatFlatUnits(asFlat(totals.atKelly))}
          </span>
          <span className={styles.figLabel}>
            the stake the system publishes &middot; {formatReturn(season.roiPerUnit, 1)}{" "}
            per unit risked
          </span>
        </div>
        <div className={styles.fig}>
          <span className={styles.figBasis}>At a flat 1 unit</span>
          <span className={styles.figValue} data-money={tone(totals.atFlat1u)}>
            {formatFlatUnits(asFlat(totals.atFlat1u))}
          </span>
          <span className={styles.figLabel}>
            the same picks at one unit each &middot; the edge with stake size
            taken out
          </span>
        </div>
        <div className={styles.fig}>
          <span className={styles.figBasis}>As actually staked</span>
          <span className={styles.figValue} data-money={tone(totals.realized)}>
            {formatFlatUnits(asFlat(totals.realized))}
          </span>
          <span className={styles.figLabel}>
            what the ledger recorded &middot; a flat unit was staked until
            quarter-Kelly went live on Jul 27
          </span>
        </div>
      </div>

      <p className={styles.note}>
        All three are exact sums on a fixed unit value, so each means the same
        on a $1,000 bankroll and a $10,000 one. Nothing here compounds:
        the system publishes a unit count, and what a unit is worth after a
        winning week is yours to decide.
      </p>

      {/* WHERE THE MONEY COMES FROM. Without this the two sections read
          as a contradiction -- the #1 play out-earns the whole slate it
          is part of. It does, because everything below it loses. */}
      {splitAgainst && splitAgainst.all.length > 0 && (
        <p className={styles.note}>
          <b>Where that comes from.</b> The night&rsquo;s top play accounts for{" "}
          <b>{formatFlatUnits(asFlat(splitAgainst.totals.atKelly))}</b> of it
          across {splitAgainst.all.length} bets. Everything below #1 is{" "}
          <b>
            {formatFlatUnits(
              asFlat(totals.atKelly - splitAgainst.totals.atKelly),
            )}
          </b>{" "}
          across the other {all.length - splitAgainst.all.length}. So the top
          play earns more than the whole slate it belongs to, which is the
          ranking working rather than an error: the rest of the board hits
          below the rate its prices demand. On these sample sizes that gap
          is suggestive and not yet significant.
        </p>
      )}

      {/* ---- the record ---- */}
      <h3 className={styles.h3}>The record</h3>
      <div className={`${styles.figures} ${styles.figuresThree}`}>
        <div className={styles.fig}>
          <span className={styles.figValue}>
            {season.wins}
            {MINUS}
            {season.losses}
          </span>
          <span className={styles.figLabel}>
            season · {(season.hitRate * 100).toFixed(1)}% against a{" "}
            {(season.breakEven * 100).toFixed(1)}% break-even
          </span>
        </div>
        <div className={styles.fig}>
          <span className={styles.figValue}>
            {last10.wins}
            {MINUS}
            {last10.losses}
          </span>
          <span className={styles.figLabel}>last 10 · far too few to read</span>
        </div>
        {/* Was max drawdown, which is a property of a COMPOUNDING bank
            and therefore of the bettor rather than the system. Replaced
            with the worst single night, which needs no bank at all. */}
        <div className={styles.fig}>
          <span className={styles.figValue} data-money={tone(worstNight)}>
            {formatUnits(worstNight)}
          </span>
          <span className={styles.figLabel}>
            worst single night at quarter-Kelly
          </span>
        </div>
      </div>

      {/* ---- since the model update: the updated model's OWN record, beside
          the season figure and never blended into it (operator, 2026-08-22;
          the weights went live 2026-08-23, see MODEL_UPDATED_FROM). ---- */}
      <h3 className={styles.h3}>Since the Aug 23 model update</h3>
      {sinceUpdate.bets === 0 ? (
        <p className={styles.note}>
          Nothing settled yet under the updated model (pooled first-inning
          pitcher xwOBA, 20 features). Its bets will be summed here as they
          settle, and they also continue the season series above.
        </p>
      ) : (
        <>
          <div className={`${styles.figures} ${styles.figuresThree}`}>
            <div className={styles.fig}>
              <span className={styles.figBasis}>Record</span>
              <span className={styles.figValue}>
                {sinceUpdate.wins}-{sinceUpdate.losses}
              </span>
              <span className={styles.figLabel}>
                {sinceUpdate.bets} settled {sinceUpdate.bets === 1 ? "bet" : "bets"} from {sinceUpdate.from}
              </span>
            </div>
            <div className={styles.fig}>
              <span className={styles.figBasis}>At quarter-Kelly</span>
              <span className={styles.figValue} data-money={tone(sinceUpdate.atKelly)}>
                {formatFlatUnits(asFlat(sinceUpdate.atKelly))}
              </span>
              <span className={styles.figLabel}>
                {sinceUpdate.staked > 0
                  ? `${formatReturn(sinceUpdate.atKelly / sinceUpdate.staked, 1)} per unit risked`
                  : "nothing staked yet"}
              </span>
            </div>
            <div className={styles.fig}>
              <span className={styles.figBasis}>At a flat 1 unit</span>
              <span className={styles.figValue} data-money={tone(sinceUpdate.atFlat1u)}>
                {formatFlatUnits(asFlat(sinceUpdate.atFlat1u))}
              </span>
              <span className={styles.figLabel}>the same nights at one unit each</span>
            </div>
          </div>
          <p className={styles.note}>
            These nights are also inside the season figures above; this block
            only reads the updated model on its own. Same rule, same prices.
          </p>
        </>
      )}

      {/* ---- the curve ---- */}
      {cumulative.length > 1 && (
        <>
          <h3 className={styles.h3}>Cumulative units</h3>
          <CumulativeChart points={cumulative} markerDate={sinceUpdate.from} />
          <p className={styles.note}>
            A running sum at quarter-Kelly, one point per settled night. Not a
            bankroll: nothing compounds, so a five-unit night in May is drawn
            the same height as a five-unit night in August. That is the whole
            reason it replaced the old equity curve, which compounded and so
            drew later results larger than earlier ones for the same result.
          </p>
        </>
      )}

      {/* ---- month by month ---- */}
      <h3 className={styles.h3}>Month by month</h3>
      <SliceTable
        rows={byMonth}
        firstCol="Month"
        label={(s) => monthName(s.key)}
      />
      <p className={styles.note}>
        <b>Per unit risked</b> is profit divided by stake, so it measures the
        quality of the bets and ignores their size. <b>Units</b> is the plain
        sum at quarter-Kelly, which also counts how much was risked &mdash;
        which is why a month of a few small winning bets can show a large
        per-unit return next to a small unit total. Neither compounds.
      </p>

      {/* ---- every bet ---- */}
      <h3 className={styles.h3}>{everyLabel}</h3>
      <div className={styles.scroller}>
        <table className={styles.table}>
          <thead>
            {/* RESULT SECOND, GAME LAST. The result was the rightmost
                column and fell off a 375px screen entirely; the game name
                is the longest column and the one whose loss costs least,
                since the date already identifies the row. */}
            <tr>
              <th scope="col">Date</th>
              <th scope="col" className={styles.right}>Result</th>
              <th scope="col" className={styles.right}>Price</th>
              <th scope="col" className={styles.right}>Stake</th>
              <th scope="col">Game</th>
            </tr>
          </thead>
          <tbody>
            {all.map((b) => {
              const [away, home] = b.game.split("@");
              return (
                <tr key={`${b.date}-${b.game}`}>
                  <th scope="row" className={styles.rowHead}>
                    {shortDate(b.date)}
                  </th>
                  {/* One bet on one night: a single point in time, so a
                      signed unit figure is a real quantity here. */}
                  <td
                    className={`${styles.right} ${styles.mono}`}
                    data-money={b.win ? "up" : "down"}
                  >
                    {b.win ? "WON" : "LOST"} {formatLevel(Math.abs(b.kellyPnl))}
                  </td>
                  <td className={`${styles.right} ${styles.mono}`}>
                    {b.odds > 0 ? `+${b.odds}` : `${MINUS}${Math.abs(b.odds)}`}
                  </td>
                  <td className={`${styles.right} ${styles.mono} ${styles.dim}`}>
                    {formatLevel(b.kellyStake)}
                  </td>
                  <td className={styles.game}>
                    {cityOf(away)} at {cityOf(home)}
                    <span className={styles.gameSide}> · {b.side}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** A plain line of the running total. No axis furniture beyond the zero
 *  rule and the endpoints, because the only questions it answers are
 *  "which way" and "how far". */
function CumulativeChart({
  points,
  markerDate,
}: {
  points: { date: string; units: number }[];
  /** A vertical rule at the first settled night on/after this date (the
   *  model update). Drawn only if such a night exists. */
  markerDate?: string;
}) {
  const W = 1000, H = 150, PAD = 4;
  const markerIdx = markerDate ? points.findIndex((p) => p.date >= markerDate) : -1;
  const vals = points.map((p) => p.units);
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const x = (i: number) => PAD + (i / (points.length - 1)) * (W - PAD * 2);
  const y = (u: number) => PAD + (1 - (u - lo) / span) * (H - PAD * 2);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.units).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <div className={styles.chartWrap}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.chart}
        role="img"
        aria-label={`Cumulative units at quarter-Kelly, ending at ${last.units.toFixed(2)} units after ${points.length} settled nights`}
      >
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} className={styles.chartZero} />
        {markerIdx > 0 && (
          <line
            x1={x(markerIdx)} x2={x(markerIdx)} y1={PAD} y2={H - PAD}
            className={styles.chartMarker}
          >
            <title>model updated {markerDate}</title>
          </line>
        )}
        <path d={d} className={styles.chartLine} />
      </svg>
      <div className={styles.chartFoot}>
        <span>{points[0].date}{markerIdx > 0 ? ` · dashed rule: model updated ${markerDate}` : ""}</span>
        <span className={styles.chartEnd} data-money={last.units >= 0 ? "up" : "down"}>
          {formatFlatUnits(asFlat(last.units))}
        </span>
        <span>{last.date}</span>
      </div>
    </div>
  );
}

function SliceTable({
  rows,
  firstCol,
  label,
}: {
  rows: TopPickSlice[];
  firstCol: string;
  label: (s: TopPickSlice) => string;
}) {
  if (rows.length === 0) return null;
  return (
    <div className={styles.scroller}>
      <table className={styles.table}>
        <thead>
          {/* MONEY FIRST, after the label. It used to sit in the last two
              columns, which is exactly the part a 375px phone cut off. */}
          <tr>
            <th scope="col">{firstCol}</th>
            <th scope="col" className={styles.right}>Per unit risked</th>
            <th scope="col" className={styles.right}>Units</th>
            <th scope="col" className={styles.right}>Record</th>
            <th scope="col" className={styles.right}>Hit</th>
            <th scope="col" className={styles.right}>Needs</th>
            <th scope="col" className={styles.right}>Staked</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.key}>
              <th scope="row" className={styles.rowHead}>{label(s)}</th>
              <td
                className={`${styles.right} ${styles.mono}`}
                data-money={tone(s.roiPerUnit)}
              >
                {formatReturn(s.roiPerUnit, 1)}
              </td>
              <td
                className={`${styles.right} ${styles.mono}`}
                data-money={tone(s.returned)}
              >
                {formatFlatUnits(asFlat(s.returned))}
              </td>
              <td className={`${styles.right} ${styles.mono}`}>
                {s.wins}
                {MINUS}
                {s.losses}
              </td>
              <td className={`${styles.right} ${styles.mono}`}>
                {(s.hitRate * 100).toFixed(1)}%
              </td>
              <td className={`${styles.right} ${styles.mono} ${styles.dim}`}>
                {(s.breakEven * 100).toFixed(1)}%
              </td>
              <td className={`${styles.right} ${styles.mono} ${styles.dim}`}>
                {formatLevel(s.staked)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
