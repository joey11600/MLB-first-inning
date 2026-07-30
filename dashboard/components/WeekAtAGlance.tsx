"use client";

import { useMemo, useState } from "react";
import type { RecSide, RebasedDay } from "@/lib/season-record";
import { rebaseLastDays } from "@/lib/season-record";
import styles from "./WeekAtAGlance.module.css";

/* ============================================================
   WEEK AT A GLANCE -- one figure, a seven-day curve, a tooltip.

   BUILT NATIVELY, NOT PASTED IN.  The operator found an analytics card
   online ("BudgetCard": big figure, smooth sparkline, hover tooltip)
   and asked for the same visual idea.  Dropping the component in was
   never an option, for two reasons that were checked rather than
   assumed:

     1. This project has NO TAILWIND AND NO SHADCN.  It is 16 CSS
        Modules files plus custom properties.  Every utility class in
        that component would have rendered as nothing -- not a broken
        style, an ABSENT one, which looks like a layout bug and gets
        debugged as one.
     2. It ships hardcoded fake data -- a $30.739 balance, an invented
        week, indigo #5B52E5 lines.  This dashboard has spent days
        removing invented numbers.  Putting a fabricated balance in the
        most prominent card on the page would undo that in one commit,
        and it would be the single most believable wrong number on the
        screen because it is the biggest.

   So: same idea, this system's materials.  CSS Modules, existing
   tokens, real data from the season record.

   WHAT THE BIG FIGURE IS, AND WHY IT IS NOT THE OBVIOUS THING.  The
   obvious headline is "sum the last seven days".  That is exactly the
   number the unit model forbids: the replay compounds the unit COUNT,
   so a 10.00u loss on a 217u bank and a 2.00u loss on a 223u bank are
   not comparable quantities and adding them is meaningless.  The naive
   sum for this window is -13.17u; the honest answer is -5.91u, which
   is what the bank actually did (223.07 -> 209.89).  See the re-basing
   block in lib/season-record.ts.

   The curve therefore plots the bank INDEXED TO 100 at window open,
   which makes the last point of the line and the headline the same
   quantity read two ways.  They cannot disagree.

   SIMULATED, AND HELD APART BY BRIGHTNESS.  Every figure here is a
   replay.  globals.css: real money is bright (--foreground / --gain /
   --loss), a back-test is neutral and dim (--muted-foreground).  Under
   the matrix palette that is the ONLY separation left, because
   --foreground and --gain are both #00FF41 -- hue cannot mark money
   when the whole page is one hue.  So nothing in this card is ever
   tone-coloured, no matter which way the week went, and the sign plus
   the word "down"/"up" carries the direction instead.
   ============================================================ */

const DAYS = 7;

/* Geometry. This is a shape, not a readable axis -- there is no y-scale
   because a seven-point curve cannot support one honestly.

   4.8:1 is a compromise picked against BOTH ends of the range, not just
   the desktop one. `meet` scales uniformly, so one viewBox has to serve
   a 1168px card and a 343px card: at 5.45:1 the phone got a 57px sliver,
   and at 3:1 the desktop got a 390px chart that pushed the equity curve
   below it off the fold. 4.8:1 lands at 243px and 71px, both of which
   are a sparkline rather than a stripe or a hero. */
const W = 720;
const H = 150;
const PAD_T = 14;
const PAD_B = 18;
const PAD_X = 6;

export function WeekAtAGlance({
  side,
  stamp,
}: {
  side: RecSide | null | undefined;
  /** Provenance line from HistoryView -- the gate and build time this
   *  replay came from. Passed in rather than rebuilt so every replay
   *  card on the page is guaranteed to describe the same build. */
  stamp?: React.ReactNode;
}) {
  const win = useMemo(() => rebaseLastDays(side, DAYS), [side]);
  const [hover, setHover] = useState<number | null>(null);

  // No record, or a week with nothing in it. Render nothing rather than
  // an empty card: PRODUCT.md wants a quiet board on a quiet night, and
  // a card announcing that it has no content is not quiet.
  if (!win || win.days.length < 2) return null;

  const { days } = win;
  const up = win.units > 0.005;
  const down = win.units < -0.005;

  // Y range over the indexed series, always including 100 so "where the
  // week started" is on the chart even during a week that only went one
  // way. Padded by 8% of the span so the line never rides the edge.
  const vals = days.map((d) => d.indexed);
  const rawMin = Math.min(100, ...vals);
  const rawMax = Math.max(100, ...vals);
  const span = rawMax - rawMin || 1;
  const yMin = rawMin - span * 0.08;
  const yMax = rawMax + span * 0.08;

  const innerW = W - PAD_X * 2;
  const innerH = H - PAD_T - PAD_B;
  const xFor = (i: number) =>
    PAD_X + (days.length === 1 ? innerW / 2 : (i / (days.length - 1)) * innerW);
  const yFor = (v: number) => PAD_T + innerH - ((v - yMin) / (yMax - yMin)) * innerH;

  const pts = days.map((d, i) => ({ x: xFor(i), y: yFor(d.indexed) }));
  const linePath = smoothPath(pts);
  const areaPath =
    `${linePath} L ${pts[pts.length - 1].x.toFixed(1)} ${(H - PAD_B).toFixed(1)}` +
    ` L ${pts[0].x.toFixed(1)} ${(H - PAD_B).toFixed(1)} Z`;
  const yBase = yFor(100);

  const hovered: RebasedDay | null = hover != null ? days[hover] ?? null : null;
  const hoveredPt = hover != null ? pts[hover] : null;

  return (
    <section className={styles.card} aria-labelledby="weekGlanceTitle">
      <div className={styles.head}>
        <div>
          <div className={styles.eyebrow} id="weekGlanceTitle">
            Last {DAYS} days · ¼-Kelly <span className="tag">Simulated</span>
          </div>
          {/* NEUTRAL INK ON PURPOSE -- see the header note. The word
              carries the direction so nothing depends on hue, which
              matters doubly here because green/red is the pair that
              collapses under red-green colour blindness. */}
          <div className={styles.fig}>
            {formatUnits(win.units)}
            <span className={styles.figWord}>
              {up ? "up" : down ? "down" : "flat"}
            </span>
          </div>
          <div className={styles.sub}>
            {/* THE PERCENT AND THE UNITS ARE THE SAME NUMBER, which is
                the point of the unit model: a unit is 1% of bank, so a
                bank that moved 5.91% moved 5.91 units for every
                subscriber regardless of what their bank is worth in
                money. Stated once, plainly, rather than shown as two
                figures that look like they should differ. */}
            {formatPct(win.pct)} of bank · {win.wins}–{win.losses} over{" "}
            {win.bets} {win.bets === 1 ? "bet" : "bets"} ·{" "}
            {shortDate(win.from)} → {shortDate(win.to)}
          </div>
        </div>
      </div>

      <div className={styles.chartWrap} onMouseLeave={() => setHover(null)}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className={styles.svg}
          /* `meet`, never `none`. A non-uniform scale stretches the
             STROKE too, so vertical runs of the curve render thicker
             than horizontal ones and the line looks hand-drawn. Uniform
             scale plus non-scaling-stroke keeps it 1.75px everywhere. */
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label={
            `Simulated bankroll over the last ${DAYS} days, ` +
            `${shortDate(win.from)} to ${shortDate(win.to)}: ` +
            `${formatUnits(win.units)}, ${formatPct(win.pct)} of bank, ` +
            `over ${win.bets} bets.`
          }
        >
          {/* The --sim-hatch rhythm (135deg, 2px on, 3px off) rebuilt
              as a paint server, because SVG `fill` takes one of those
              and not a CSS image. See the note on .area. */}
          <defs>
            <pattern
              id="weekGlanceHatch"
              width="5" height="5"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(135)"
            >
              <line x1="0" y1="0" x2="0" y2="5" className={styles.hatchLine} />
            </pattern>
          </defs>

          {/* WHERE THE WEEK STARTED. The one reference line on the
              chart, because "is this above or below where I began" is
              the only question a seven-point curve can answer honestly. */}
          <line x1={PAD_X} x2={W - PAD_X} y1={yBase} y2={yBase} className={styles.baseline} />

          <path d={areaPath} className={styles.area} />
          <path d={linePath} className={styles.line} />

          {days.map((d, i) => (
            <circle
              key={d.date}
              cx={pts[i].x}
              cy={pts[i].y}
              r={hover === i ? 4.5 : 2.5}
              className={styles.dot}
              data-active={hover === i ? "1" : undefined}
            />
          ))}

          {hoveredPt && (
            <line
              x1={hoveredPt.x} x2={hoveredPt.x}
              y1={PAD_T} y2={H - PAD_B}
              className={styles.crosshair}
            />
          )}

          {/* HIT TARGETS. One transparent band per point, rather than
              one mousemove handler doing coordinate maths: the SVG
              scales with the card, so any hand-rolled client-x -> data-x
              conversion has to track that scale and gets it wrong the
              first time the layout changes. Bands cannot drift. */}
          {days.map((d, i) => {
            const half = innerW / Math.max(days.length - 1, 1) / 2;
            const x0 = i === 0 ? 0 : pts[i].x - half;
            const x1 = i === days.length - 1 ? W : pts[i].x + half;
            return (
              <rect
                key={`hit-${d.date}`}
                x={x0} y={0} width={Math.max(x1 - x0, 1)} height={H}
                className={styles.hit}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                tabIndex={0}
                role="img"
                aria-label={dayReadout(d)}
              />
            );
          })}
        </svg>

        {hovered && hoveredPt && (
          <div
            className={styles.tip}
            style={{
              left: `${(hoveredPt.x / W) * 100}%`,
              // Flip the tooltip to the left of the crosshair on the
              // right-hand third so it never leaves the card.
              transform:
                hoveredPt.x / W > 0.66
                  ? "translate(-100%, 0)"
                  : hoveredPt.x / W < 0.12
                    ? "translate(0, 0)"
                    : "translate(-50%, 0)",
            }}
            role="status"
          >
            <div className={styles.tipDate}>{longDate(hovered.date)}</div>
            <div className={styles.tipRow}>
              <span className={styles.tipKey}>Day</span>
              <span className={styles.tipVal}>{formatUnits(hovered.units)}</span>
            </div>
            <div className={styles.tipRow}>
              <span className={styles.tipKey}>Week to date</span>
              <span className={styles.tipVal}>
                {formatUnits(hovered.indexed - 100)}
              </span>
            </div>
          </div>
        )}

        <div className={styles.axis} aria-hidden>
          <span>{shortDate(win.from)}</span>
          <span>{shortDate(win.to)}</span>
        </div>
      </div>

      {stamp}

      <p className={styles.foot}>
        A unit is 1% of your bankroll, so these are the same numbers for
        every follower whatever their bank is worth. The curve is the bank
        indexed to 100 at the start of the window, which is why its last
        point and the figure above are the same quantity.
        {/* NOT A FOOTNOTE, A RECONCILIATION. The record holds bets on a
            side this bank does not stake, and a reader who counts rows
            elsewhere will get a different total. Say so on the face of
            the card rather than letting them find it. */}
        {win.offSideBets > 0 && (
          <>
            {" "}
            {win.offSideBets} NRFI {win.offSideBets === 1 ? "bet is" : "bets are"}{" "}
            in the record for this window and {win.offSideBets === 1 ? "is" : "are"}{" "}
            excluded here — NRFI has been switched off since 2026-06-07, so the
            bank never staked {win.offSideBets === 1 ? "it" : "them"}.
          </>
        )}
        {win.assumed > 0 && (
          <>
            {" "}
            {win.assumed} of {win.bets} had no captured DraftKings price and settled
            against an assumed −125.
          </>
        )}
      </p>
    </section>
  );
}

/* ------------- geometry ------------- */

/**
 * Catmull-Rom through the points, emitted as cubic Béziers.
 *
 * The control points are CLAMPED to each segment's own y-range. Plain
 * Catmull-Rom overshoots around a sharp reversal, and on a bankroll
 * chart an overshoot draws a dip the bank never took -- a smoothing
 * artefact rendered at the same weight as data. Clamping costs a little
 * roundness at the corners and buys a curve that never claims a value
 * outside the observations.
 */
function smoothPath(p: { x: number; y: number }[]): string {
  if (p.length === 0) return "";
  if (p.length === 1) return `M ${p[0].x.toFixed(1)} ${p[0].y.toFixed(1)}`;
  let d = `M ${p[0].x.toFixed(1)} ${p[0].y.toFixed(1)}`;
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[i - 1] ?? p[i];
    const p1 = p[i];
    const p2 = p[i + 1];
    const p3 = p[i + 2] ?? p2;
    const lo = Math.min(p1.y, p2.y);
    const hi = Math.max(p1.y, p2.y);
    const c1y = clamp(p1.y + (p2.y - p0.y) / 6, lo, hi);
    const c2y = clamp(p2.y - (p3.y - p1.y) / 6, lo, hi);
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    d +=
      ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)},` +
      ` ${c2x.toFixed(1)} ${c2y.toFixed(1)},` +
      ` ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/* ------------- formatters ------------- */

/** U+2212 MINUS, not a hyphen, so a negative reads as a number. */
function formatUnits(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) < 0.005) return "0.00u";
  return `${n > 0 ? "+" : "−"}${Math.abs(n).toFixed(2)}u`;
}

function formatPct(f: number): string {
  if (!Number.isFinite(f)) return "—";
  if (Math.abs(f) < 0.00005) return "0.00%";
  return `${f > 0 ? "+" : "−"}${Math.abs(f * 100).toFixed(2)}%`;
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short", day: "numeric", timeZone: "UTC",
  });
}

function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", timeZone: "UTC",
  });
}

/** Screen-reader text for one point. Mirrors the tooltip exactly, so a
 *  keyboard user gets the same three facts a mouse user does. */
function dayReadout(d: RebasedDay): string {
  return (
    `${longDate(d.date)}: ${formatUnits(d.units)} on the day, ` +
    `${formatUnits(d.indexed - 100)} for the week to date.`
  );
}
