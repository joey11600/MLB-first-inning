/**
 * THE #1 PLAY UNDER TODAY'S RULES — the section that answers "is the top
 * pick any good, run the way I run it now".
 *
 * TWO OF ITS MONEY FIGURES ARE FACTS AND TWO ARE SIMULATIONS. The games,
 * prices and results are all real ledger data; the STAKES are not. Since
 * 2026-08-03 the section sizes every night at today's quarter-Kelly,
 * where 85 of the original 92 bets were really placed at 1.00u. That is
 * a ~9x difference (+7.49u realized against +68.23u simulated), so the
 * simulated figures carry a dashed rule and the word "simulated", and
 * `totals.realized` sits beside them. The operator SELLS these picks:
 * a re-staked backtest printed as realized profit is the failure this
 * section is built to avoid.
 *
 * EVERY SLICE COMPOUNDS FROM 100 SEPARATELY. A month that opened on a
 * 210u bank and one that opened on 100u would otherwise look like wildly
 * different performances for identical betting. Re-basing each slice
 * makes the rows comparable to one another, which is the only reason to
 * put them in a table together.
 */
import type { TopPickReport, TopPickSlice } from "@/lib/top-pick";
import {
  asFlat,
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

export function TopPickHistory({ report }: { report: TopPickReport | null }) {
  if (!report || report.all.length === 0) return null;
  const { bank, last10, byMonth, all, totals, noEdgeUnderKelly } = report;
  const season = report.windows[0];

  return (
    <section className={styles.wrap} aria-labelledby="topPickHistTitle">
      <h2 className={styles.title} id="topPickHistTitle">
        The #1 play, under today&rsquo;s rules
      </h2>
      <p className={styles.lead}>
        The top YRFI play of each night since May 26, when the live model
        weights were fit, staked at today&rsquo;s quarter-Kelly. One bet a
        night, {all.length} of them. NRFI is excluded because it has been
        switched off since June 7, so on a night whose top play was an NRFI
        pick the best YRFI play is counted instead.
        {noEdgeUnderKelly > 0 && (
          <>
            {" "}
            {noEdgeUnderKelly} further night
            {noEdgeUnderKelly === 1 ? "" : "s"} had a top play that
            today&rsquo;s staking rule would not bet at all, because there was
            no edge at the price actually paid.
          </>
        )}
      </p>

      {/* ---- UNITS PROFIT, ON ALL THREE BASES ----
          These can and do differ in SIGN, so none of them may appear
          without its basis named. A unit total is exact whenever the
          unit's dollar value never moved between the bets; it is the
          COMPOUNDING case, not the passage of time, that breaks the
          arithmetic. See the correction note in lib/units.ts. */}
      <h3 className={styles.h3}>Units profit</h3>
      {/* THE BASIS IS NAMED ABOVE THE FIGURE, NOT ONLY UNDER IT.
          All three carry a "+" and a "u" and the same bright green, so
          nothing in the figures themselves says they are three different
          KINDS of number. PRODUCT.md's central design problem is exactly
          this. The eyebrow goes first because it is also the order the
          figure has to be spoken: basis, then number. */}
      <div className={styles.figures}>
        <div className={styles.fig}>
          <span className={styles.figBasis}>Flat stake</span>
          <span
            className={styles.figValue}
            data-money={tone(totals.atFlat1u)}
          >
            {formatFlatUnits(asFlat(totals.atFlat1u))}
          </span>
          <span className={styles.figLabel}>
            betting a flat 1 unit every night · the model&rsquo;s edge with
            stake size taken out
          </span>
        </div>
        <div className={styles.fig}>
          <span className={`${styles.figBasis} ${styles.figBasisSim}`}>
            Quarter-Kelly · simulated
          </span>
          <span
            className={`${styles.figValue} ${styles.figValueSim}`}
            data-money={tone(totals.atKelly)}
          >
            {formatFlatUnits(asFlat(totals.atKelly))}
          </span>
          <span className={styles.figLabel}>
            these nights sized the way the system sizes a bet today · NOT
            money anyone made
          </span>
        </div>
        <div className={styles.fig}>
          <span className={styles.figBasis}>Actually staked</span>
          <span
            className={styles.figValue}
            data-money={tone(totals.realized)}
          >
            {formatFlatUnits(asFlat(totals.realized))}
          </span>
          <span className={styles.figLabel}>
            what the ledger really risked and really returned · Kelly only
            went live on Jul 27, so most of these were 1.00u
          </span>
        </div>
        <div className={styles.fig}>
          <span className={`${styles.figBasis} ${styles.figBasisSim}`}>
            Compounded · simulated
          </span>
          <span
            className={`${styles.figValue} ${styles.figValueSim}`}
            data-money={tone(bank.ret)}
          >
            {returnAsUnits(bank.ret)}
          </span>
          <span className={styles.figLabel}>
            the quarter-Kelly run re-sized to 1% of the running bank ·{" "}
            {formatBankGrowth(bank.start, bank.end)}
          </span>
        </div>
      </div>

      <p className={styles.note}>
        <b>Two of these are facts and two are simulations, and the gap is
        large.</b> Flat 1u and Actually staked are what happened. The
        quarter-Kelly figures are what today&rsquo;s staking rule WOULD have
        returned on the same games, at a median stake of 5 units rather than
        the 1 unit most of them were really placed at &mdash; which is why
        they are roughly nine times the realized number. They are the right
        thing to look at when deciding what this system is worth going
        forward, and the wrong thing to call profit. Every figure is an exact
        sum on a fixed unit value, so all of them mean the same on a $1,000
        bank and a $10,000 one; only the last assumes you re-size after every
        result.
      </p>

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
        <div className={styles.fig}>
          <span className={styles.figValue} data-money={tone(bank.maxDrawdown)}>
            {formatReturn(bank.maxDrawdown, 1)}
          </span>
          <span className={styles.figLabel}>
            deepest fall from a running high · peak {bank.peak.toFixed(2)}u
          </span>
        </div>
      </div>

      {/* ---- month by month ---- */}
      <h3 className={styles.h3}>Month by month</h3>
      <SliceTable
        rows={byMonth}
        firstCol="Month"
        label={(s) => monthName(s.key)}
      />
      {/* The two money columns answer different questions and can look
          wildly apart: April was 2 bets at 1u, so it returned +75.5% per
          unit risked while moving a 100u bank by 1.52u. Neither is
          wrong; printing them without this sentence would be. */}
      <p className={styles.note}>
        <b>Per unit risked</b> is profit divided by stake, so it measures the
        quality of the bets and ignores their size. <b>100u becomes</b> also
        counts how much was risked, which is why a month of two small winning
        bets can show a large per-unit return next to a bank that barely
        moved. Each month compounds from its own 100u so the rows can be
        compared with each other.
      </p>

      {/* ---- every bet ---- */}
      <h3 className={styles.h3}>Every #1 play, most recent first</h3>
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
              <th scope="col" className={styles.right}>Stake (sim)</th>
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
            <th scope="col" className={styles.right}>100u becomes</th>
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
                data-money={tone(s.ret)}
              >
                {s.bankEnd.toFixed(2)}u
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
