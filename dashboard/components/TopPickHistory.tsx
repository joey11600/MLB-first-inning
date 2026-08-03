/**
 * THE #1 PLAY, IN FULL — the section that answers "is the top pick any
 * good", with the tables to back it.
 *
 * SITS UNDER TopPickCard, WHICH IT DOES NOT DUPLICATE. That card is the
 * verdict: four time windows, a record, a confidence interval. This is
 * the working: how the bank actually moved, month by month, side by
 * side, and every settled bet in order. The card is for deciding whether
 * to trust the number; this is for finding out why it is what it is, and
 * for pulling a figure to quote.
 *
 * THE "UNITS PROFIT" QUESTION, ANSWERED THE ONLY WAY IT CAN BE. The
 * operator asked for "units profit" on the #1 pick. There is no such
 * quantity: 1 unit is 1% of bankroll, so a unit won in April and a unit
 * won in July are amounts in different currencies, and `formatUnits`
 * refuses a summed figure at COMPILE time to stop exactly this
 * (lib/units.ts). What there IS, and what this section prints, is where
 * the bank went: 100u in, X out, which is the same information and is
 * true. Because the bank is 100 units by definition, that return can
 * legitimately be read with a "u" on it, which is what `returnAsUnits`
 * is for.
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
  const { bank, last10, byMonth, bySide, all, totals, currentSystem } = report;
  const season = report.windows[0];

  return (
    <section className={styles.wrap} aria-labelledby="topPickHistTitle">
      <h2 className={styles.title} id="topPickHistTitle">
        The #1 play · full history
      </h2>
      <p className={styles.lead}>
        Every night&rsquo;s top-ranked play, settled at the price actually
        captured. One bet a night, {all.length} of them.
      </p>

      {/* ---- UNITS PROFIT, ON ALL THREE BASES ----
          These can and do differ in SIGN, so none of them may appear
          without its basis named. A unit total is exact whenever the
          unit's dollar value never moved between the bets; it is the
          COMPOUNDING case, not the passage of time, that breaks the
          arithmetic. See the correction note in lib/units.ts. */}
      <h3 className={styles.h3}>Units profit</h3>
      <div className={styles.figures}>
        <div className={styles.fig}>
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
          <span
            className={styles.figValue}
            data-money={tone(totals.atPublishedStakes)}
          >
            {formatFlatUnits(asFlat(totals.atPublishedStakes))}
          </span>
          <span className={styles.figLabel}>
            betting the unit count actually published each night, without
            re-sizing · what a follower made
          </span>
        </div>
        <div className={styles.fig}>
          <span className={styles.figValue} data-money={tone(bank.ret)}>
            {returnAsUnits(bank.ret)}
          </span>
          <span className={styles.figLabel}>
            re-sizing every bet to 1% of the running bank ·{" "}
            {formatBankGrowth(bank.start, bank.end)}
          </span>
        </div>
      </div>

      <p className={styles.note}>
        All three are the same bets. The first two are exact sums and mean
        the same thing on any bankroll: one unit is 1% of the bank you
        started with, so <b>{formatFlatUnits(asFlat(totals.atFlat1u))}</b> is{" "}
        {Math.abs(totals.atFlat1u).toFixed(2)}% of a $1,000 bank and the same
        percentage of a $10,000 one. The third is the only one that assumes
        you re-size after every result, which changes what a unit is worth
        as you go and is why it can disagree with the other two.
      </p>

      {/* ---- the record ---- */}
      <h3 className={styles.h3}>The record</h3>
      <div className={styles.figures}>
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

      {/* ---- TODAY'S RULE, APPLIED BACKWARDS ----
          The season row above includes 15 nights whose #1 was an NRFI
          play, and NRFI has been off since 2026-06-07 for losing in
          every band. It also includes April and May games scored by
          model weights replaced on 2026-05-26. This block is the same
          ledger under today's selection rule. It is NOT a backtest, and
          the copy says so: only re-scoring can undo the calibrator and
          gate changes, which is what tools/season_replay.py does. */}
      {currentSystem && (
        <>
          <h3 className={styles.h3}>Under the current system</h3>
          <div className={styles.figures}>
            <div className={styles.fig}>
              <span className={styles.figValue}>
                {currentSystem.wins}
                {MINUS}
                {currentSystem.losses}
              </span>
              <span className={styles.figLabel}>
                since {shortDate(currentSystem.from)} · YRFI only, the rule
                actually in force today
              </span>
            </div>
            <div className={styles.fig}>
              <span className={styles.figValue}>
                {(currentSystem.hitRate * 100).toFixed(1)}%
              </span>
              <span className={styles.figLabel}>
                against a {(currentSystem.breakEven * 100).toFixed(1)}%
                break-even · 95% range{" "}
                {(currentSystem.ciLo * 100).toFixed(0)}&ndash;
                {(currentSystem.ciHi * 100).toFixed(0)}%
              </span>
            </div>
            <div className={styles.fig}>
              <span
                className={styles.figValue}
                data-money={tone(currentSystem.flatRoi)}
              >
                {formatFlatUnits(
                  asFlat(currentSystem.flatRoi * currentSystem.bets),
                )}
              </span>
              <span className={styles.figLabel}>
                betting a flat 1 unit on each of these {currentSystem.bets}
              </span>
            </div>
          </div>
          <p className={styles.note}>
            The model weights in production were fit on{" "}
            {shortDate(currentSystem.from)}, and NRFI betting was switched off
            on Jun 7 for losing in every band, so on nights whose top play was
            an NRFI pick the top YRFI play is counted instead. That much can be
            applied backwards honestly: you simply would not have placed those
            bets.{" "}
            <b>This is not a backtest.</b> The calibrator swap of Jul 28 and
            the gate move of Jul 30 changed which games qualify as a play at
            all, and no filter on past rows can undo that. For that, re-score
            the season with <code>tools/season_replay.py --top-only --since{" "}
            {currentSystem.from}</code>, which walks the calibrator forward so
            no result is used before it was known.
            {currentSystem.ciLo <= currentSystem.breakEven && (
              <>
                {" "}
                Note the 95% range still crosses the break-even rate: {currentSystem.bets}{" "}
                bets is not enough to rule out luck, whichever way it points.
              </>
            )}
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

      {/* ---- by side ---- */}
      <h3 className={styles.h3}>By side</h3>
      <SliceTable rows={bySide} firstCol="Side" label={(s) => s.key} />
      <p className={styles.note}>
        NRFI has been switched off since 2026-06-07, so its row is a closed
        book rather than an ongoing result.
      </p>

      {/* ---- every bet ---- */}
      <h3 className={styles.h3}>Every #1 play, most recent first</h3>
      <div className={styles.scroller}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Game</th>
              <th scope="col">Side</th>
              <th scope="col" className={styles.right}>Price</th>
              <th scope="col" className={styles.right}>Stake</th>
              <th scope="col" className={styles.right}>Result</th>
            </tr>
          </thead>
          <tbody>
            {all.map((b) => {
              const [away, home] = b.game.split("@");
              return (
                <tr key={`${b.date}-${b.game}`}>
                  <td>{shortDate(b.date)}</td>
                  <td className={styles.game}>
                    {cityOf(away)} at {cityOf(home)}
                  </td>
                  <td>{b.side}</td>
                  <td className={`${styles.right} ${styles.mono}`}>
                    {b.odds > 0 ? `+${b.odds}` : b.odds}
                  </td>
                  <td className={`${styles.right} ${styles.mono} ${styles.dim}`}>
                    {formatLevel(b.unitsRisked)}
                  </td>
                  {/* One bet on one night: a single point in time, so a
                      signed unit figure is a real quantity here. */}
                  <td
                    className={`${styles.right} ${styles.mono}`}
                    data-money={b.win ? "up" : "down"}
                  >
                    {b.win ? "WON" : "LOST"} {formatLevel(Math.abs(b.pnl))}
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
          <tr>
            <th scope="col">{firstCol}</th>
            <th scope="col" className={styles.right}>Record</th>
            <th scope="col" className={styles.right}>Hit</th>
            <th scope="col" className={styles.right}>Needs</th>
            <th scope="col" className={styles.right}>Staked</th>
            <th scope="col" className={styles.right}>Per unit risked</th>
            <th scope="col" className={styles.right}>100u becomes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.key}>
              <th scope="row" className={styles.rowHead}>{label(s)}</th>
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
