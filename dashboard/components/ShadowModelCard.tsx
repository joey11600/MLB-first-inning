/**
 * THE SHADOW MODEL, beside the live one (2026-09-04).
 *
 * Reads the finished `ShadowReport` the history page builds server-side
 * from the ledger's `shadow_*` columns and shows the operator what the
 * second model WOULD have done on the same nights at the same prices. It
 * never shows a stake as if it were placed: the shadow's figures are
 * labelled "would have", and the live model's booked line is the only
 * real money on the card.
 *
 * Styling reuses TopPickHistory's classes on purpose, so the two sections
 * read as one family: the basis is named above each figure, figures are
 * monospace, and every total is a fixed-basis `FlatUnits`.
 */
import { asFlat, formatFlatUnits, MINUS, type FlatUnits } from "@/lib/units";
import type { ShadowReport, ShadowSide, ShadowNo1 } from "@/lib/shadow-compare";
import styles from "./TopPickHistory.module.css";

function tone(x: number): "up" | "down" | "flat" {
  if (x > 0.0005) return "up";
  if (x < -0.0005) return "down";
  return "flat";
}

function pct(x: number | null): string {
  return x == null ? MINUS : `${(x * 100).toFixed(1)}%`;
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

function Side({ label, s, booked }: { label: string; s: ShadowSide; booked?: { units: FlatUnits; staked: number } }) {
  return (
    <div className={styles.figures}>
      {booked && (
        <div className={styles.fig}>
          <span className={styles.figBasis}>{label} · as actually staked</span>
          <span className={styles.figValue} data-money={tone(booked.units)}>
            {formatFlatUnits(booked.units)}
          </span>
          <span className={styles.figLabel}>
            {s.bets} bet{s.bets === 1 ? "" : "s"} · {booked.staked.toFixed(1)}u risked
          </span>
        </div>
      )}
      <div className={styles.fig}>
        <span className={styles.figBasis}>{label} · same rule, quarter-Kelly</span>
        <span className={styles.figValue} data-money={tone(s.sameRule)}>
          {formatFlatUnits(s.sameRule)}
        </span>
        <span className={styles.figLabel}>
          {s.wins}&ndash;{s.losses} · hit {pct(s.hit)} · needs {pct(s.breakEven)}
        </span>
      </div>
      <div className={styles.fig}>
        <span className={styles.figBasis}>{label} · flat 1 unit</span>
        <span className={styles.figValue} data-money={tone(s.flat)}>
          {formatFlatUnits(s.flat)}
        </span>
        <span className={styles.figLabel}>stated {pct(s.stated)} on average</span>
      </div>
    </div>
  );
}

function No1({ label, n }: { label: string; n: ShadowNo1 }) {
  return (
    <div className={styles.fig}>
      <span className={styles.figBasis}>{label}</span>
      <span className={styles.figValue} data-money={tone(n.sameRule)}>
        {formatFlatUnits(n.sameRule)}
      </span>
      <span className={styles.figLabel}>
        {n.wins}&ndash;{n.nights - n.wins} over {n.nights} night{n.nights === 1 ? "" : "s"} · hit {pct(n.hit)}
      </span>
    </div>
  );
}

export function ShadowModelCard({ report }: { report: ShadowReport | null }) {
  const id = "shadowModelTitle";
  if (!report) {
    return (
      <section className={styles.wrap} aria-labelledby={id}>
        <h2 className={styles.title} id={id}>The shadow model</h2>
        <p className={styles.lead}>
          Since Sept 4 the system scores every game a second time with a
          candidate model and records its answer next to the live one. It is
          never published and never bet. No game has been graded with a
          shadow value yet, so there is nothing to compare; this section fills
          in as nights settle.
        </p>
      </section>
    );
  }
  const r = report;
  const n = r.no1;
  return (
    <section className={styles.wrap} aria-labelledby={id}>
      <h2 className={styles.title} id={id}>The shadow model</h2>
      <p className={styles.lead}>
        Since {shortDate(r.since)} the system has scored every game twice: once
        with the live model, whose pick is the one published and staked, and
        once with a candidate that swaps the raw last-ten no-run rate for a
        properly shrunk version of it. The candidate is never published and
        never bet. What follows is what it <em>would</em> have done on the same
        nights at the same prices, sized by the same quarter-Kelly rule as the
        live model, so the two can be read against each other. {r.gradedRows}{" "}
        graded game{r.gradedRows === 1 ? "" : "s"} so far. A few weeks of this
        cannot settle which model is better; it proves the candidate behaves
        and builds the record for the offseason decision.
      </p>

      <h3 className={styles.h3}>Live model</h3>
      <Side label="Live" s={r.live} booked={{ units: r.live.booked, staked: r.live.bookedStaked }} />

      <h3 className={styles.h3}>Shadow model, would-have-been</h3>
      <Side label="Shadow" s={r.shadow} />

      <h3 className={styles.h3}>The night&rsquo;s No.1, each model&rsquo;s own</h3>
      <div className={styles.figures}>
        <No1 label="Live No.1, same rule" n={n.live} />
        <No1 label="Shadow No.1, same rule" n={n.shadow} />
        <div className={styles.fig}>
          <span className={styles.figBasis}>Nights both had a No.1</span>
          <span className={styles.figValue}>{n.nightsBoth}</span>
          <span className={styles.figLabel}>
            same game on {n.sameGameOnBoth} · live {n.liveOnBoth.wins}&ndash;{n.liveOnBoth.nights - n.liveOnBoth.wins}
            {" "}vs shadow {n.shadowOnBoth.wins}&ndash;{n.shadowOnBoth.nights - n.shadowOnBoth.wins}
          </span>
        </div>
      </div>
      <p className={styles.note}>
        The paired line is the one that matters: on nights where both models
        made a top play, same nights and same prices, so the luck of which
        nights each one bet is removed.
      </p>

      {r.nights.length > 0 && (
        <>
          <h3 className={styles.h3}>By night, most recent first</h3>
          <div className={styles.scroller}>
            <table className={styles.table}>
              <thead>
                <tr className={styles.rowHead}>
                  <th>Night</th>
                  <th>Live No.1</th>
                  <th>Shadow No.1</th>
                </tr>
              </thead>
              <tbody>
                {[...r.nights].reverse().slice(0, 21).map((x) => (
                  <tr key={x.date}>
                    <td className={styles.mono}>{shortDate(x.date)}</td>
                    <td>
                      {x.live ? (
                        <>
                          <span className={styles.game}>{x.live.game}</span>{" "}
                          <span className={styles.mono} data-money={tone(x.live.pnl)}>
                            {x.live.won ? "W" : "L"} {formatFlatUnits(asFlat(x.live.pnl))}
                          </span>
                        </>
                      ) : (
                        <span className={styles.dim}>no play</span>
                      )}
                    </td>
                    <td>
                      {x.shadow ? (
                        <>
                          <span className={styles.game}>{x.shadow.game}</span>{" "}
                          <span className={styles.mono} data-money={tone(x.shadow.pnl)}>
                            {x.shadow.won ? "W" : "L"} {formatFlatUnits(asFlat(x.shadow.pnl))}
                          </span>
                          {x.sameGame && <span className={styles.dim}> · same</span>}
                        </>
                      ) : (
                        <span className={styles.dim}>no play</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {r.agreement.length > 0 && (
        <p className={styles.note}>
          Where the two models landed on every graded game:{" "}
          {r.agreement.slice(0, 5).map((a, i) => (
            <span key={a.key}>
              {i > 0 ? " · " : ""}
              <span className={styles.mono}>{a.count}</span> {a.key}
            </span>
          ))}
          .
        </p>
      )}

      {r.tonight.date && (
        <p className={styles.note}>
          Tonight ({shortDate(r.tonight.date)}): live STRONG on{" "}
          {r.tonight.live.length ? r.tonight.live.join(", ") : "nothing"}; shadow STRONG on{" "}
          {r.tonight.shadow.length ? r.tonight.shadow.join(", ") : "nothing"}.
        </p>
      )}
    </section>
  );
}
