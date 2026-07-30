import type { TopPickReport, TopPickWindow } from "@/lib/top-pick";
import { formatLevel, formatReturn, MINUS } from "@/lib/units";
import styles from "./TopPickCard.module.css";

/* ============================================================
   THE #1 PICK — the top-ranked play of each night, and what it
   really did.

   REAL MONEY, AND IT LOOKS LIKE IT. Every other performance surface on
   this page is the replay: today's model re-scoring history, rendered
   DIM because globals.css says a back-test is never tone-coloured. This
   card is the opposite — it is the ledger, so its figures are BRIGHT
   and they DO carry the money hues. Under the matrix palette that
   brightness contrast is the only thing left that separates "this
   happened to your money" from "this is a simulation", so the two cards
   sitting on one page is the distinction teaching itself.

   THE MONEY FIGURE IS A RATIO. Returned ÷ staked, never a unit total:
   1 unit = 1% of bankroll, so units from different dates are amounts in
   different currencies (lib/units.ts). It is also the honest figure for
   a follower on any bankroll.

   WHY THE CONFIDENCE INTERVAL IS ON THE CARD RATHER THAN IN A FOOTNOTE.
   One bet a night makes small windows genuinely uninformative — a
   7-day column is six bets and its interval runs about sixty points
   wide. Printing "33.3%" next to "69.2%" with nothing else invites the
   reader to believe the model broke this week. The interval is what
   stops that, so it sits in the table.
   ============================================================ */

/** "2026-04-29" -> "Apr 29".
 *
 *  The year is the same on both ends of every window and on every row,
 *  so it was 10 characters of pure repetition -- and at 375px it was
 *  overflowing the column by 29px, which `white-space: nowrap` turned
 *  into a clip rather than a wrap. Shorter AND more readable. */
function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short", day: "numeric", timeZone: "UTC",
  });
}

function toneOf(w: TopPickWindow): "up" | "down" | "flat" {
  if (w.roiPerUnit > 0.0005) return "up";
  if (w.roiPerUnit < -0.0005) return "down";
  return "flat";
}

/** Does the window's hit rate clear the price it paid, with confidence? */
function verdict(w: TopPickWindow): string {
  if (w.ciLo > w.breakEven) return "beats its price";
  if (w.ciHi < w.breakEven) return "below its price";
  return "too few bets to tell";
}

export function TopPickCard({ report }: { report: TopPickReport | null }) {
  if (!report || report.windows.length === 0) return null;
  const season = report.windows[0];

  return (
    <section className={styles.card} aria-labelledby="topPickTitle">
      <div className={styles.head}>
        <div>
          <div className={styles.eyebrow} id="topPickTitle">
            The #1 pick · real money
          </div>
          <div className={styles.sub}>
            The top-ranked play of each night, as it actually settled —
            placed bets, captured prices, real stakes. Not the replay.
          </div>
        </div>
        <div className={styles.headFig}>
          {/* THE HEADLINE IS THE RECORD, not the money. Over a season of
              one-bet nights the record is the stable quantity; the money
              swings on how big two or three Kelly stakes happened to be. */}
          <div className={styles.figBig} data-money={toneOf(season)}>
            {season.wins}{MINUS}{season.losses}
          </div>
          <div className={styles.figSub}>
            {(season.hitRate * 100).toFixed(1)}% · needs{" "}
            {(season.breakEven * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      <div className={styles.tableWrap}>
        <div className={styles.theadRow}>
          <div>Window</div>
          <div className={styles.right}>Record</div>
          <div className={styles.right}>Hit</div>
          <div className={styles.right}>Needs</div>
          <div className={styles.right}>Staked</div>
          <div className={styles.right}>Return</div>
          <div className={styles.verdictCell}>95% range on hit rate</div>
        </div>
        {report.windows.map((w) => (
          <div className={styles.row} key={w.label}>
            <div className={styles.windowCell}>
              <span className={styles.windowName}>{w.label}</span>
              <span className={styles.windowSpan}>
                {shortDate(w.from)} → {shortDate(w.to)}
              </span>
            </div>
            <div className={`${styles.right} ${styles.mono}`} data-cell="record">
              {w.wins}{MINUS}{w.losses}
            </div>
            <div className={`${styles.right} ${styles.mono}`} data-cell="hit" data-label="hit">
              {(w.hitRate * 100).toFixed(1)}%
            </div>
            <div className={`${styles.right} ${styles.mono} ${styles.dim}`} data-cell="needs" data-label="needs">
              {(w.breakEven * 100).toFixed(1)}%
            </div>
            <div className={`${styles.right} ${styles.mono} ${styles.dim}`} data-cell="staked" data-label="staked">
              {formatLevel(w.staked)}
            </div>
            {/* REAL money, so it keeps its hue -- see the header note. */}
            <div className={`${styles.right} ${styles.mono}`} data-cell="return" data-money={toneOf(w)}>
              {formatReturn(w.roiPerUnit, 1)}
            </div>
            <div className={styles.verdictCell}>
              <span className={styles.ciBar} aria-hidden>
                <span
                  className={styles.ciRange}
                  style={{
                    left: `${Math.max(0, w.ciLo * 100)}%`,
                    width: `${Math.max(1, (w.ciHi - w.ciLo) * 100)}%`,
                  }}
                />
                <span
                  className={styles.ciMark}
                  style={{ left: `${w.breakEven * 100}%` }}
                />
              </span>
              <span className={styles.ciText}>
                {(w.ciLo * 100).toFixed(0)}–{(w.ciHi * 100).toFixed(0)}% ·{" "}
                {verdict(w)}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className={styles.streak}>
        <span className={styles.streakLabel}>Last 12</span>
        {report.recent
          .slice()
          .reverse()
          .map((b) => (
            <span
              key={`${b.date}-${b.game}`}
              className={styles.pip}
              data-win={b.win ? "1" : undefined}
              title={`${b.date} ${b.game} ${b.side} at ${
                b.odds > 0 ? "+" : ""
              }${b.odds} — ${b.win ? "won" : "lost"} ${b.unitsRisked.toFixed(2)}u staked`}
            >
              {b.win ? "W" : "L"}
            </span>
          ))}
      </div>

      <p className={styles.foot}>
        <b>Return</b> is units returned divided by units staked, not a total —
        a unit is 1% of your bankroll, so units from different dates are
        different money and adding them would not be a quantity.{" "}
        <b>Needs</b> is the break-even rate the prices you actually paid
        demand. The bar shows the 95% range the record is consistent with;
        the tick is that break-even. A range that straddles the tick means
        the window is too small to conclude anything — which is normally
        true of everything under 30 days when you bet once a night.
        {season.excludedNoPrice > 0 && (
          <>
            {" "}
            {season.excludedNoPrice} night
            {season.excludedNoPrice === 1 ? "" : "s"} had no captured
            DraftKings price on the top pick and {season.excludedNoPrice === 1
              ? "is"
              : "are"}{" "}
            excluded rather than settled against a made-up number.
          </>
        )}
      </p>
    </section>
  );
}
