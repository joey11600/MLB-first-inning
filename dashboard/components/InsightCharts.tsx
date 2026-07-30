"use client";

/**
 * InsightCharts -- the three charts approved in the 2026-07-28 spec.
 *
 * WHY ONE MODULE
 * --------------
 * All three are hand-rolled SVG in the EquityCurveChart idiom
 * (viewBox + padL/padR/padT/padB, path strings built with
 * `.map().join(" ")`, classes from a CSS module).  They share the
 * chart chrome, the guarded formatters and the "never print a
 * simulated figure in a money colour" rule, so they share a file.
 * No chart library, no new dependency.
 *
 *   1. <ReliabilityCurve />  -- model probability vs what actually
 *      happened, on the YRFI axis.  Main page, Zone 3, above
 *      DayReconcile.  Self-fetches /api/calibration unless a `data`
 *      prop is supplied.
 *   2. <UnderwaterChart />   -- how far below the high-water mark,
 *      every day.  /history, beneath the equity curve.
 *   3. <DivergenceBar />     -- where today's model and the actual
 *      ledger disagree.  /history, beneath the underwater plot.
 *
 * MOUNTING: each component renders its OWN complete card (header,
 * chart, stats, footnotes).  Mount it directly -- do NOT wrap it in
 * another .chartCard or you get a card inside a card.
 *
 * TWO RULES THIS FILE MUST NEVER BREAK
 * ------------------------------------
 *   1. NEVER INVENT A NUMBER.  Every figure below is interpolated
 *      from props.  Nothing is hardcoded, and every .toFixed() call
 *      goes through fmtU / fmtPct / fmtInt, which return an em dash
 *      for a missing or non-finite value rather than "0.00".
 *   2. NEVER TONE-COLOUR A SIMULATED FIGURE.  Peach (--gain) and rust
 *      (--loss) mean real money that moved.  The replay compounds an
 *      imaginary bank; it renders in --muted-foreground whatever its
 *      sign, behind a SIMULATED tag.  Same rule DayReconcile states in
 *      its header.
 *
 * COLOUR: only --gain / --loss / --attn / --side-nrfi / --foreground /
 * --muted-foreground / --border are referenced, per globals.css:63-66.
 * The reliability curve uses NO HUE AT ALL -- a calibration miss is
 * not money.  No new colour is introduced anywhere, so no new contrast
 * ratio needed measuring; the ones relied on are listed in the CSS.
 */

import { useEffect, useMemo, useState } from "react";
import styles from "./InsightCharts.module.css";

/* ═══════════════════════════════════════════════════════════════════
   Shared guarded formatters.

   Every one of these returns an em dash for a missing value.  A
   figure that reads "0.00u" must mean the number really is zero --
   the operator reads a zero as "nothing happened", and printing one
   for "we don't know" is how a dashboard starts lying quietly.
   ═══════════════════════════════════════════════════════════════════ */

const EM_DASH = "—";
/** U+2212 MINUS SIGN, not a hyphen -- it lines up in tabular-nums. */
const MINUS = "−";

function isNum(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

/** "+2.91u" / "−17.95u" / "0.00u" / "—" */
function fmtU(n: number | null | undefined, digits = 2): string {
  if (!isNum(n)) return EM_DASH;
  const sign = n > 0.0000001 ? "+" : n < -0.0000001 ? MINUS : "";
  return `${sign}${Math.abs(n).toFixed(digits)}u`;
}

/** Magnitude only, already signed by the caller's copy: "18.31u". */
function fmtUAbs(n: number | null | undefined, digits = 2): string {
  if (!isNum(n)) return EM_DASH;
  return `${Math.abs(n).toFixed(digits)}u`;
}

/** A 0..1 probability as a percentage: 0.6018 -> "60.2%". */
function fmtPct(p: number | null | undefined, digits = 1): string {
  if (!isNum(p)) return EM_DASH;
  return `${(p * 100).toFixed(digits)}%`;
}

function fmtInt(n: number | null | undefined): string {
  if (!isNum(n)) return EM_DASH;
  return Math.round(n).toLocaleString("en-US");
}

/** "$1,830" from units, guarded.  Whole dollars -- cents on a
 *  drawdown figure are noise. */
function fmtDollars(units: number | null | undefined, perUnit: number): string {
  if (!isNum(units) || !isNum(perUnit)) return EM_DASH;
  return `$${Math.round(Math.abs(units) * perUnit).toLocaleString("en-US")}`;
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-19" -> "Jul 19".  Parsed by regex, never by `new Date(iso)`:
 *  these are ET slate dates, not instants, and the Date constructor
 *  reads a bare ISO date as UTC midnight, which slips a day for anyone
 *  west of Greenwich. */
function shortDate(iso: string | null | undefined): string {
  if (!iso) return EM_DASH;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${MON[Number(m[2]) - 1]} ${Number(m[3])}`;
}

const plural = (n: number, word: string) => (n === 1 ? word : `${word}s`);

/** Two-sided Wilson score interval for a binomial proportion.
 *
 *  Used only as a FALLBACK when the server did not send wLo/wHi with a
 *  bin.  Wilson rather than normal-approximation because several bins
 *  sit near n=20, where the normal interval runs off the end of [0,1]
 *  and would draw a bar taller than the chart. */
function wilson(p: number, n: number, z = 1.959963985): { lo: number; hi: number } | null {
  if (!isNum(p) || !isNum(n) || n <= 0) return null;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const centre = p + z2 / (2 * n);
  const half = z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n);
  return { lo: Math.max(0, (centre - half) / denom), hi: Math.min(1, (centre + half) / denom) };
}

/** Shared "nothing to draw" line.  Every chart in this file degrades to
 *  one short sentence rather than an empty box -- an empty box reads as
 *  a broken page, and the operator's standing complaint is that things
 *  appear to vanish. */
function EmptyLine({ children }: { children: React.ReactNode }) {
  return <div className={styles.empty}>{children}</div>;
}

/* ═══════════════════════════════════════════════════════════════════
   CHART 1 -- RELIABILITY CURVE

   Decision it changes: whether to keep betting the band.  This is the
   only chart on either page that can move a threshold.

   WHY THE YRFI AXIS.  He bets STRONG YRFI when nrfi_prob is below the
   gate.  On an NRFI axis his region is the left tail and
   "over-confident about the bet" reads as "under-confident about
   NRFI".  Plotting P(YRFI) = 1 - nrfi_prob puts his region on the
   right, where a dot BELOW the diagonal means over-confidence -- the
   intuitive read.

   WHY EQUAL-WIDTH BINS, NOT DECILES.  The calibrator has flat steps:
   hundreds of games share a single probability.  An equal-count split
   through a step gives a different answer depending only on sort
   order.  Equal-width bins do not have that failure mode.  The rule is
   stated on the chart face so nobody has to take it on trust.
   ═══════════════════════════════════════════════════════════════════ */

export interface CalibrationBin {
  /** bin lower edge on the P(YRFI) axis, inclusive */
  lo: number;
  /** bin upper edge, exclusive */
  hi: number;
  /** games in the bin */
  n: number;
  /** mean model P(YRFI) across the bin -- the dot's x */
  meanPred: number;
  /** share of those games where a run actually scored -- the dot's y */
  actual: number;
  /** Wilson 95% bounds on `actual`.  Derived locally when absent. */
  wLo?: number;
  wHi?: number;
}

export interface CalibrationData {
  bins: CalibrationBin[];
  /** mean implied probability of the DK prices actually paid */
  breakEven: number;
  /** the STRONG YRFI gate on the P(YRFI) axis (1 - strongYrfiP) */
  gate: number;
  /** aggregate over every bin at or above the gate.  Derived from
   *  `bins` when the server does not send it. */
  betRegion?: { n: number; pred: number; actual: number; wLo?: number; wHi?: number };
  /** games with both a model probability and a graded first inning */
  totalGames?: number;
  binWidth?: number;
  minBinN?: number;
  /** bins suppressed for having fewer than minBinN games */
  droppedBins?: number;
  /** how many real-priced bets the break-even average was taken over */
  breakEvenBets?: number;
}

export interface ReliabilityCurveProps {
  /** Supply to render synchronously.  Omit and the component fetches
   *  /api/calibration itself. */
  data?: CalibrationData | null;
  /** Override the fetch endpoint (tests, or a future per-window route). */
  endpoint?: string;
}

export function ReliabilityCurve({
  data: injected,
  endpoint = "/api/calibration",
}: ReliabilityCurveProps) {
  const [fetched, setFetched] = useState<CalibrationData | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">(
    injected ? "done" : "idle",
  );

  useEffect(() => {
    if (injected) return;
    let alive = true;
    setState("loading");
    fetch(endpoint, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: CalibrationData) => {
        if (!alive) return;
        setFetched(j);
        setState("done");
      })
      .catch(() => {
        if (alive) setState("error");
      });
    return () => {
      alive = false;
    };
  }, [injected, endpoint]);

  const data = injected ?? fetched;

  // Nothing at all until the fetch resolves.  A skeleton here would
  // push Zone 3 around after hydration, which is the same complaint
  // the ops panel earned.
  if (!injected && (state === "idle" || state === "loading")) return null;

  if (!data || !Array.isArray(data.bins)) {
    return (
      <section className={styles.card}>
        <Head
          eyebrow="Calibration"
          title="Model probability vs what actually happened · YRFI"
        />
        <EmptyLine>
          Not enough data yet — the calibration figures could not be read.
        </EmptyLine>
      </section>
    );
  }

  return <ReliabilityCurveBody data={data} />;
}

function ReliabilityCurveBody({ data }: { data: CalibrationData }) {
  const gate = isNum(data.gate) ? data.gate : null;
  const breakEven = isNum(data.breakEven) ? data.breakEven : null;

  // Keep only bins we can actually place on the canvas.
  const bins = useMemo(
    () =>
      (data.bins ?? []).filter(
        (b) => isNum(b?.meanPred) && isNum(b?.actual) && isNum(b?.n) && b.n > 0,
      ),
    [data.bins],
  );

  // Bet region: prefer the server's aggregate, otherwise pool the bins
  // that sit at or above the gate.  Pooling is exact -- these are
  // counts, so a games-weighted mean of the bins IS the region mean.
  const region = useMemo(() => {
    if (data.betRegion && isNum(data.betRegion.n) && data.betRegion.n > 0) {
      const r = data.betRegion;
      const w = isNum(r.wLo) && isNum(r.wHi)
        ? { lo: r.wLo, hi: r.wHi }
        : wilson(r.actual, r.n);
      return { n: r.n, pred: r.pred, actual: r.actual, wLo: w?.lo ?? null, wHi: w?.hi ?? null };
    }
    if (gate == null) return null;
    const inBand = bins.filter((b) => b.meanPred >= gate);
    const n = inBand.reduce((a, b) => a + b.n, 0);
    if (n <= 0) return null;
    const pred = inBand.reduce((a, b) => a + b.meanPred * b.n, 0) / n;
    const actual = inBand.reduce((a, b) => a + b.actual * b.n, 0) / n;
    const w = wilson(actual, n);
    return { n, pred, actual, wLo: w?.lo ?? null, wHi: w?.hi ?? null };
  }, [data.betRegion, bins, gate]);

  if (bins.length === 0) {
    return (
      <section className={styles.card}>
        <Head
          eyebrow="Calibration"
          title="Model probability vs what actually happened · YRFI"
        />
        <EmptyLine>
          Not enough data yet — no probability band has enough graded games to plot.
        </EmptyLine>
      </section>
    );
  }

  /* ---- layout, EquityCurveChart idiom ---- */
  const W = 720, H = 380;
  const padL = 56, padR = 20, padT = 18, padB = 48;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  // Nominal domain 0.30 -> 0.75, widened if the data or either rule
  // would otherwise fall off the canvas.
  const candidates: number[] = [];
  for (const b of bins) {
    candidates.push(b.meanPred);
    candidates.push(isNum(b.wLo) ? b.wLo : b.actual);
    candidates.push(isNum(b.wHi) ? b.wHi : b.actual);
    candidates.push(b.actual);
  }
  if (gate != null) candidates.push(gate);
  if (breakEven != null) candidates.push(breakEven);
  const lo = Math.min(0.3, Math.floor(Math.min(...candidates) * 20) / 20);
  const hi = Math.max(0.75, Math.ceil(Math.max(...candidates) * 20) / 20);
  const span = hi - lo || 1;

  const xFor = (v: number) => padL + ((v - lo) / span) * innerW;
  const yFor = (v: number) => padT + innerH - ((v - lo) / span) * innerH;

  // Ticks every 5 points across whatever domain we ended up with.
  const ticks: number[] = [];
  for (let t = Math.ceil(lo * 20) / 20; t <= hi + 1e-9; t += 0.05) {
    ticks.push(Math.round(t * 100) / 100);
  }

  const diag = `M ${xFor(lo).toFixed(1)} ${yFor(lo).toFixed(1)} L ${xFor(hi).toFixed(1)} ${yFor(hi).toFixed(1)}`;

  const totalBins = bins.length + (isNum(data.droppedBins) ? data.droppedBins : 0);

  /* ---- the verdict sentence, chosen by where the interval sits ---- */
  let verdictSentence: string | null = null;
  if (region && isNum(region.wLo) && isNum(region.wHi) && breakEven != null) {
    const holdsBE = region.wLo <= breakEven && region.wHi >= breakEven;
    const holdsPred = isNum(region.pred) && region.wLo <= region.pred && region.wHi >= region.pred;
    const head =
      `The 95% interval on that ${fmtPct(region.actual)} runs ` +
      `${fmtPct(region.wLo)} to ${fmtPct(region.wHi)}. `;
    if (region.wLo > breakEven) {
      verdictSentence =
        head +
        `It sits entirely above the ${fmtPct(breakEven)} you need to break even, ` +
        `so on this sample the band clears the bar.`;
    } else if (region.wHi < breakEven) {
      verdictSentence =
        head +
        `It sits entirely below the ${fmtPct(breakEven)} you need to break even, ` +
        `so on this sample the band is losing money.`;
    } else if (holdsBE && holdsPred) {
      verdictSentence =
        head +
        `It contains both the model's ${fmtPct(region.pred)} and the ` +
        `${fmtPct(breakEven)} break-even, so this is an edge that is positive ` +
        `but not yet proven.`;
    } else {
      verdictSentence =
        head +
        `It contains the ${fmtPct(breakEven)} you need to break even, ` +
        `so the edge is not yet proven.`;
    }
  }

  return (
    <section className={styles.card}>
      <div className={styles.headRow}>
        <Head
          eyebrow="Calibration"
          title="Model probability vs what actually happened · YRFI"
        />
        <div className={styles.legend}>
          <span className={styles.legendItem}>
            <span className={styles.legendDot} data-in="1" /> the band you bet
          </span>
          <span className={styles.legendItem}>
            <span className={styles.legendDot} data-in="0" /> outside it
          </span>
          <span className={styles.legendItem}>
            <span className={styles.legendBar} /> 95% range
          </span>
        </div>
      </div>

      <div className={styles.scroll}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className={styles.calibSvg}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label={
            "Reliability curve: model probability of a run in the first inning " +
            "against how often a run actually scored."
          }
        >
          {/* The band he actually bets. */}
          {gate != null && (
            <rect
              x={xFor(gate)}
              y={padT}
              width={Math.max(0, xFor(hi) - xFor(gate))}
              height={innerH}
              className={styles.betBand}
            />
          )}

          {/* Grid + axis ticks */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={xFor(t)} x2={xFor(t)} y1={padT} y2={padT + innerH} className={styles.grid} />
              <line x1={padL} x2={padL + innerW} y1={yFor(t)} y2={yFor(t)} className={styles.grid} />
              <text x={xFor(t)} y={H - padB + 18} textAnchor="middle" className={styles.axis}>
                {(t * 100).toFixed(0)}%
              </text>
              <text x={padL - 8} y={yFor(t) + 4} textAnchor="end" className={styles.axis}>
                {(t * 100).toFixed(0)}%
              </text>
            </g>
          ))}

          {/* Perfect-calibration diagonal */}
          <path d={diag} className={styles.diag} />

          {/* Break-even: the average price actually paid. */}
          {breakEven != null && (
            <>
              <line
                x1={padL}
                x2={padL + innerW}
                y1={yFor(breakEven)}
                y2={yFor(breakEven)}
                className={styles.beRule}
              />
              <text x={padL + 6} y={yFor(breakEven) - 6} className={styles.ruleLabel}>
                break-even {fmtPct(breakEven)} — the prices you actually paid
              </text>
            </>
          )}

          {/* Gate */}
          {gate != null && (
            <>
              <line
                x1={xFor(gate)}
                x2={xFor(gate)}
                y1={padT}
                y2={padT + innerH}
                className={styles.gateRule}
              />
              {/* Flip the label to the left of the rule when the gate
                  sits far enough right that the text would run off. */}
              <text
                x={xFor(gate) + (xFor(gate) > padL + innerW * 0.72 ? -6 : 6)}
                y={padT + 12}
                textAnchor={xFor(gate) > padL + innerW * 0.72 ? "end" : "start"}
                className={styles.ruleLabel}
              >
                gate {fmtPct(gate, 0)}
              </text>
            </>
          )}

          {/* Wilson intervals, drawn under the dots. */}
          {bins.map((b, i) => {
            const w = isNum(b.wLo) && isNum(b.wHi)
              ? { lo: b.wLo, hi: b.wHi }
              : wilson(b.actual, b.n);
            if (!w) return null;
            return (
              <line
                key={`ci-${i}`}
                x1={xFor(b.meanPred)}
                x2={xFor(b.meanPred)}
                y1={yFor(w.lo)}
                y2={yFor(w.hi)}
                className={styles.ci}
              />
            );
          })}

          {/* One dot per bin, area proportional to games. */}
          {bins.map((b, i) => {
            const inBand = gate != null && b.meanPred >= gate;
            const r = Math.max(4, Math.min(13, Math.sqrt(b.n) * 0.9));
            return (
              <circle
                key={`dot-${i}`}
                cx={xFor(b.meanPred)}
                cy={yFor(b.actual)}
                r={r}
                className={inBand ? styles.dotIn : styles.dotOut}
                data-n={b.n}
                data-pred={b.meanPred.toFixed(4)}
                data-actual={b.actual.toFixed(4)}
              >
                <title>
                  {`Model said ${fmtPct(b.meanPred)} · a run scored ${fmtPct(b.actual)} of the time · ${fmtInt(b.n)} ${plural(b.n, "game")}`}
                </title>
              </circle>
            );
          })}

          {/* Axis titles */}
          <text
            x={padL + innerW / 2}
            y={H - 8}
            textAnchor="middle"
            className={styles.axisTitle}
          >
            Model P(YRFI) = 1 − nrfi_prob
          </text>
          <text
            x={-(padT + innerH / 2)}
            y={13}
            textAnchor="middle"
            transform="rotate(-90)"
            className={styles.axisTitle}
          >
            How often a run scored
          </text>
        </svg>
      </div>

      {/* Three figures.  None toned: a calibration miss is not money. */}
      <div className={styles.trio}>
        <div>
          <div className={styles.trioLabel}>Model says</div>
          <span className="figStat">{fmtPct(region?.pred)}</span>
        </div>
        <div>
          <div className={styles.trioLabel}>Reality</div>
          <span className="figStat">{fmtPct(region?.actual)}</span>
        </div>
        <div>
          <div className={styles.trioLabel}>Break-even</div>
          <span className="figStat">{fmtPct(breakEven)}</span>
        </div>
      </div>

      <p className="copy">
        In the band you bet — model P(YRFI) above {fmtPct(gate, 0)} — the model says{" "}
        <b className={styles.fig}>{fmtPct(region?.pred)}</b> and reality delivered{" "}
        <b className={styles.fig}>{fmtPct(region?.actual)}</b> across{" "}
        <b className={styles.fig}>{fmtInt(region?.n)}</b> {plural(region?.n ?? 0, "game")}. You need{" "}
        <b className={styles.fig}>{fmtPct(breakEven)}</b> to break even at the prices you
        actually paid.
      </p>

      {verdictSentence && (
        <p className="copy">
          {verdictSentence} The range narrows as games accumulate; it does not narrow by
          staring at it.
        </p>
      )}

      <p className={styles.foot}>
        {isNum(data.binWidth) && isNum(data.minBinN) ? (
          <>
            Equal-width bands of {data.binWidth.toFixed(3)} on the model&rsquo;s own
            probability; a band with fewer than {fmtInt(data.minBinN)} games is not drawn
            {isNum(data.droppedBins) ? <> ({fmtInt(data.droppedBins)} of {fmtInt(totalBins)})</> : null}.{" "}
          </>
        ) : null}
        Equal-count bands (deciles) are deliberately not used — the model&rsquo;s probability
        has flat steps where hundreds of games share one value, and splitting an equal count
        through a step gives a different answer depending only on sort order.
      </p>

      <p className={styles.foot}>
        {isNum(data.totalGames) ? (
          <>
            {fmtInt(data.totalGames)} games with a model probability and a graded first
            inning.{" "}
          </>
        ) : null}
        The break-even line is the average implied probability of the DraftKings prices
        actually paid
        {isNum(data.breakEvenBets) ? (
          <> on {fmtInt(data.breakEvenBets)} real-priced STRONG YRFI {plural(data.breakEvenBets, "bet")}</>
        ) : null}
        , not the −110 reference rate.
      </p>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   CHART 2 -- UNDERWATER PLOT

   Decision it changes: whether he keeps running quarter-Kelly.  At
   compounding, drawdown DEPTH and drawdown AGE are the two numbers
   that decide it, and an equity curve buries both -- a line that
   wanders can be read optimistically.  An underwater plot cannot.

   Zero sits at the TOP and depth grows downward, so depth is read
   against a fixed axis rather than inferred from shading.
   ═══════════════════════════════════════════════════════════════════ */

export interface UnderwaterPoint {
  date: string;
  /** THE BANK LEVEL at that day's close -- not cumulative units.
   *
   *  Changed 2026-07-30 with the unit re-basing. The depth of a hole
   *  measured in units drifts with the bank it was dug in: a 20-unit
   *  fall from a 220u bank and a 20-unit fall from a 110u bank are 9%
   *  and 18%, i.e. the same "units" describing twice the damage. Depth
   *  is a RATIO now, and this field carries the level it is taken
   *  against. */
  bank: number;
}

export interface UnderwaterChartProps {
  /** roi.realPricedCumulativePL -- the money series.  NOT the raw
   *  column, which folds in bets settled against a fabricated −110. */
  series: UnderwaterPoint[];
  /** first date stakes stopped being flat 1u (thresholds.kellyEpoch) */
  stakeEpoch?: string | null;
  /** what to call that epoch on the rule label */
  stakeEpochLabel?: string;
  /** real.sim.maxDrawdownPct from season_record.json.  SIMULATED --
   *  rendered in --muted-foreground behind a tag, never toned. */
  simMaxDrawdownPct?: number | null;
  /** bankroll the percentage is quoted against */
  bankrollUnits?: number;
  /** dollars per unit, for the plain-English translation */
  dollarsPerUnit?: number;
  /** One sentence naming WHICH bets make up the hole, e.g. the side
   *  and the count.  Rendered verbatim when supplied; omitted when
   *  not, because this file cannot derive it from the series. */
  composition?: string;
}

export function UnderwaterChart({
  series,
  stakeEpoch = null,
  stakeEpochLabel = "¼-Kelly",
  simMaxDrawdownPct = null,
  bankrollUnits = 100,
  dollarsPerUnit = 100,
  composition,
}: UnderwaterChartProps) {
  const pts = useMemo(
    () => (series ?? []).filter((p) => p && typeof p.date === "string" && isNum(p.bank)),
    [series],
  );

  const model = useMemo(() => {
    if (pts.length === 0) return null;
    // Every one of these is annotated on purpose: they are assigned
    // inside the forEach callback, and an un-annotated `let` would let
    // the compiler infer the initializer's literal type.
    let peak: number = -Infinity;
    let peakDate: string | null = null;
    // The peak that DEFINES the current drawdown -- i.e. the most
    // recent day the curve was at its high-water mark.
    let currentPeakDate: string | null = null;
    let currentPeakIdx: number = 0;
    let deepest: number = 0;
    let deepestDate: string | null = null;
    const depth: { date: string; depth: number }[] = [];

    pts.forEach((p, i) => {
      if (p.bank > peak) {
        peak = p.bank;
        peakDate = p.date;
        currentPeakDate = p.date;
        currentPeakIdx = i;
      }
      // A FRACTION of the peak it fell from, so the number means the
      // same thing in April on a 100u bank and in July on a 230u one.
      const d = peak > 0 ? Math.max(0, (peak - p.bank) / peak) : 0;
      if (d <= 1e-9) {
        currentPeakDate = p.date;
        currentPeakIdx = i;
      }
      if (d > deepest) {
        deepest = d;
        deepestDate = p.date;
      }
      depth.push({ date: p.date, depth: d });
    });

    const lastIdx = pts.length - 1;
    const current = depth[lastIdx]?.depth ?? 0;
    return {
      depth,
      peak: peak === -Infinity ? 0 : peak,
      peakDate: peakDate as string | null,
      currentPeakDate: currentPeakDate as string | null,
      deepest,
      deepestDate: deepestDate as string | null,
      current,
      daysUnderWater: current > 1e-9 ? lastIdx - currentPeakIdx : 0,
      // 5 basis points of the peak, i.e. the same "close enough" the
      // old 0.005 absolute meant against a ~100u bank. Rescaled with
      // the units, not left behind -- an absolute 0.005 against a
      // fractional depth would treat a 0.4% hole as no hole at all.
      currentIsDeepest: current > 1e-9 && Math.abs(current - deepest) < 0.0005,
    };
  }, [pts]);

  if (!model || model.depth.length < 2) {
    return (
      <section className={styles.card}>
        <Head
          eyebrow="Drawdown"
          title="How far below the high-water mark, every day"
        />
        <EmptyLine>
          Not enough data yet — fewer than two days with a captured DraftKings price.
        </EmptyLine>
      </section>
    );
  }

  const { depth } = model;

  /* ---- layout, same padding idiom as EquityCurveChart ---- */
  const W = 1100, H = 200;
  const padL = 56, padR = 12, padT = 16, padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  // 1% floor so a flat, never-underwater run still gets a sane axis.
  const depthMax = Math.max(0.01, ...depth.map((d) => d.depth));
  const stepX = innerW / Math.max(depth.length, 1);
  const xFor = (i: number) => padL + (i + 0.5) * stepX;
  const yFor = (d: number) => padT + (d / depthMax) * innerH;

  const linePath = depth
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xFor(i).toFixed(1)} ${yFor(d.depth).toFixed(1)}`)
    .join(" ");

  const areaPath =
    `M ${xFor(0).toFixed(1)} ${padT.toFixed(1)} ` +
    depth.map((d, i) => `L ${xFor(i).toFixed(1)} ${yFor(d.depth).toFixed(1)}`).join(" ") +
    ` L ${xFor(depth.length - 1).toFixed(1)} ${padT.toFixed(1)} Z`;

  const tickVals = [0, depthMax / 3, (depthMax * 2) / 3, depthMax];
  const labelEvery = Math.max(1, Math.ceil(depth.length / 10));

  const epochIdx = stakeEpoch ? depth.findIndex((d) => d.date === stakeEpoch) : -1;
  // How much of the hole arrived on the epoch day itself.
  const epochDrop =
    epochIdx > 0 ? depth[epochIdx].depth - depth[epochIdx - 1].depth : null;

  const lastIdx = depth.length - 1;
  const troughY = yFor(depth[lastIdx].depth);
  // The label always sits to the LEFT of the final dot -- the dot is
  // by definition at the right edge, so anchoring it any other way
  // runs the text off the canvas.

  const pctOfBank =
    // DEPTH IS ALREADY A SHARE OF BANK (2026-07-30), so this used to
    // divide a fraction by 100 and print 0.1% where the truth was 11%.
    // Kept as an explicit conversion rather than deleted, because the
    // sub-line's job is the MONEY translation: a unit is 1% of bank, so
    // an 11% hole is 11 units is $1,100 at $100 a unit.
    isNum(bankrollUnits) && bankrollUnits > 0 ? model.current * 100 : null;

  return (
    <section className={styles.card}>
      <div className={styles.headRow}>
        <Head
          eyebrow="Drawdown"
          title="How far below the high-water mark, every day"
          sub="real captured prices only"
        />
        <div className={styles.legend}>
          <span className={styles.legendItem}>
            <span className={styles.legendLine} /> depth below the high, % of peak
          </span>
        </div>
      </div>

      <div className={styles.scroll}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className={styles.wideSvg}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Underwater plot: how far below the high-water mark, as a percentage of the peak, day by day"
        >
          {/* Depth ticks.  Zero is the high-water line, at the top. */}
          {tickVals.map((v, i) => (
            <g key={i}>
              <line
                x1={padL}
                x2={W - padR}
                y1={yFor(v)}
                y2={yFor(v)}
                className={i === 0 ? styles.uwHighWater : styles.grid}
              />
              <text x={padL - 8} y={yFor(v) + 4} textAnchor="end" className={styles.uwAxis}>
                {v <= 1e-9 ? "0%" : `${MINUS}${(v * 100).toFixed(1)}%`}
              </text>
            </g>
          ))}

          <path d={areaPath} className={styles.uwArea} />
          <path d={linePath} className={styles.uwLine} />

          {/* The day the stake size changed.  Before it a loss is one
              unit; after it, whatever Kelly sized. */}
          {epochIdx >= 0 && (
            <>
              <line
                x1={xFor(epochIdx)}
                x2={xFor(epochIdx)}
                y1={padT}
                y2={padT + innerH}
                className={styles.uwEpoch}
              />
              <text
                x={xFor(epochIdx) - 8}
                y={padT + 14}
                textAnchor="end"
                className={styles.uwEpochLabel}
              >
                {stakeEpochLabel} from {shortDate(stakeEpoch)}
              </text>
            </>
          )}

          {/* Where he stands right now. */}
          <circle cx={xFor(lastIdx)} cy={troughY} r={4} className={styles.uwTroughDot} />
          <text
            x={xFor(lastIdx) - 10}
            y={troughY + 4}
            textAnchor="end"
            className={styles.uwTroughLabel}
          >
            {model.current > 1e-9
              ? `${MINUS}${(model.current * 100).toFixed(1)}% · ${fmtInt(model.daysUnderWater)} ${plural(model.daysUnderWater, "day")}`
              : "at the high-water mark"}
          </text>

          {/* X-axis dates */}
          {depth.map((d, i) =>
            i % labelEvery === 0 ? (
              <text
                key={i}
                x={xFor(i)}
                y={H - padB + 18}
                textAnchor="middle"
                className={styles.uwAxis}
              >
                {d.date.slice(5)}
              </text>
            ) : null,
          )}
        </svg>
      </div>

      <div className={styles.statRow}>
        <div>
          <div className={styles.statLabel}>Current drawdown</div>
          <span className="figStat" data-money={model.current > 0.00005 ? "down" : "flat"}>
            {model.current > 0.00005
              ? `${MINUS}${(model.current * 100).toFixed(1)}%`
              : "at the high"}
          </span>
          <span className={styles.statSub}>
            {model.current > 0.00005 && pctOfBank != null ? (
              <>
                {fmtUAbs(model.current * bankrollUnits)} of your{" "}
                {fmtInt(bankrollUnits)}u bankroll ·{" "}
                {fmtDollars(model.current * bankrollUnits, dollarsPerUnit)} at{" "}
                {fmtDollars(1, dollarsPerUnit)} a unit
              </>
            ) : (
              <>nothing given back since the high</>
            )}
          </span>
        </div>
        <div>
          <div className={styles.statLabel}>Deepest drawdown</div>
          <span className="figStat" data-money={model.deepest > 0.00005 ? "down" : "flat"}>
            {model.deepest > 0.00005
              ? `${MINUS}${(model.deepest * 100).toFixed(1)}%`
              : "0.0%"}
          </span>
          <span className={styles.statSub}>
            {model.deepest <= 0.00005
              ? "the curve has never been below its high"
              : model.currentIsDeepest
                ? "the current one is the deepest"
                : `bottomed on ${shortDate(model.deepestDate)}`}
          </span>
        </div>
        <div>
          <div className={styles.statLabel}>Days under water</div>
          <span className="figStat">{fmtInt(model.daysUnderWater)}</span>
          <span className={styles.statSub}>
            {model.daysUnderWater > 0
              ? `since the high on ${shortDate(model.currentPeakDate)}`
              : "sitting at the high-water mark"}
          </span>
        </div>
      </div>

      {composition && <p className="copy">{composition}</p>}

      {epochIdx >= 0 && isNum(epochDrop) && epochDrop > 0.00005 && (
        <p className="copy">
          {/* Both figures are fractions of the peak now, so the
              threshold moved with them -- 0.005 against a fraction is
              half a percent, which would have hidden most epoch days. */}
          {shortDate(stakeEpoch)} is the first stake bigger than one unit. That single day
          accounts for{" "}
          <b className={styles.fig}>{(epochDrop * 100).toFixed(1)}%</b> of the{" "}
          <b className={styles.fig}>{(model.current * 100).toFixed(1)}%</b> hole — one
          ordinary loss at several times the old size, not several ordinary losses.
        </p>
      )}

      {isNum(simMaxDrawdownPct) && (
        <p className={styles.foot}>
          <span className="tag">Simulated</span>{" "}
          The model replay&rsquo;s own compounding run reports a{" "}
          <b className={styles.figMuted}>{simMaxDrawdownPct.toFixed(1)}%</b> deepest
          drawdown. That is a simulation on an imaginary bank, not this curve.
        </p>
      )}

      <p className={styles.foot}>
        Built only from bets that had a real captured DraftKings price. Bets with no
        captured price are left out entirely rather than settled against a stand-in −110,
        which is what used to make this page read the opposite sign.
      </p>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   CHART 3 -- REPLAY VS LEDGER DIVERGENCE

   Decision it changes: two.  First, how much of the record to discount
   -- the bets today's model would decline are a different population
   from the ones it agrees with, and they can carry opposite P&L.
   Second, it auto-detects the class of failure where a STRONG pick
   grades before its lock window commits and books nothing until it is
   healed.

   The replay segment is HATCHED and its units are muted.  That money
   never moved.
   ═══════════════════════════════════════════════════════════════════ */

export type DivergenceState = "agree" | "you-only" | "replay-only" | "idle";

export interface DivergenceSegment {
  state: DivergenceState;
  count: number;
  /** ledger units for the two ledger rows; replay units for the replay
   *  row (which is simulated); null for "flagged, nobody bet".
   *
   *  SUMMED ACROSS BETS AND ACROSS DATES, so it is never rendered as a
   *  unit total (2026-07-30). It is divided by `count` and shown as a
   *  return per unit staked -- see segReturn() below. Under the
   *  re-based unit model a 1u win when the unit was worth $100 and a 1u
   *  win when it was worth $150 are different money, so the sum is not
   *  a quantity; the ratio is. */
  units?: number | null;
  /** true when `units` is a back-test figure, not money that moved */
  simulated?: boolean;
  /** optional override for the default English label */
  label?: string;
}

export interface DivergenceSkipCode {
  code: string;
  count: number;
  /** optional override for the default English wording */
  label?: string;
}

export interface DivergenceGame {
  date: string;
  game: string;
  side?: string | null;
}

export interface DivergenceSummary {
  segments: DivergenceSegment[];
  skipCodes?: DivergenceSkipCode[];
  /** games flagged STRONG in the ledger that never carried a bet */
  neverCommitted?: DivergenceGame[];
  window?: { from?: string | null; to?: string | null; games?: number | null };
}

export interface DivergenceBarProps {
  summary: DivergenceSummary | null | undefined;
  /** The mandatory "these two figures count different populations"
   *  sentence.  Overridable so whoever mounts it can name the exact
   *  other figure on the page. */
  contrastNote?: string;
}

/** Fixed and meaningful order: agreement first, then the two kinds of
 *  disagreement, then the games nobody touched.  Same order and the
 *  same wording DayReconcile uses, so the two views read alike. */
/** Return per unit staked for one segment, assuming a unit a bet --
 *  the same assumption and the same reasoning as the zone table on
 *  /history. `null` when there is nothing to divide. */
function segReturn(units: number | null | undefined, count: number): number | null {
  if (!isNum(units) || !isNum(count) || count <= 0) return null;
  return units / count;
}

function segReturnText(units: number | null | undefined, count: number): string {
  const r = segReturn(units, count);
  if (r == null) return EM_DASH;
  if (Math.abs(r) < 0.00005) return "0.00%";
  return `${r > 0 ? "+" : MINUS}${Math.abs(r * 100).toFixed(1)}%`;
}

const DIV_ORDER: { state: DivergenceState; label: string }[] = [
  { state: "agree",       label: "Both acted" },
  { state: "you-only",    label: "You bet, replay passed" },
  { state: "replay-only", label: "Replay bet, you didn't" },
  { state: "idle",        label: "Flagged, nobody bet" },
];

/** Machine skip codes translated to something a non-developer reads
 *  without a glossary.  Unknown codes degrade to the raw code with the
 *  underscores taken out, never to a blank chip. */
const SKIP_WORDS: Record<string, string> = {
  gate:           "not confident enough",
  lambda_floor:   "too few runs projected",
  lambda_ceiling: "too many runs projected",
  kelly_no_edge:  "no edge at the price",
  daily_cap:      "daily risk cap",
  no_price:       "no price captured",
  unscored:       "no scored history",
};

function skipWords(code: string, override?: string): string {
  if (override) return override;
  return SKIP_WORDS[code] ?? code.replace(/_/g, " ");
}

export function DivergenceBar({ summary, contrastNote }: DivergenceBarProps) {
  const segs = useMemo(() => {
    const byState = new Map<DivergenceState, DivergenceSegment>();
    for (const s of summary?.segments ?? []) {
      if (!s || !isNum(s.count)) continue;
      byState.set(s.state, s);
    }
    return DIV_ORDER.map((g) => {
      const found = byState.get(g.state);
      return {
        state: g.state,
        label: found?.label ?? g.label,
        count: found && isNum(found.count) ? found.count : 0,
        units: found && isNum(found.units) ? found.units : null,
        simulated: found?.simulated ?? g.state === "replay-only",
      };
    });
  }, [summary]);

  const total = segs.reduce((a, s) => a + s.count, 0);

  if (!summary || total === 0) {
    return (
      <section className={styles.card}>
        <Head
          eyebrow="Replay vs ledger"
          title="Where today's model and your actual bets disagree"
        />
        <EmptyLine>
          Not enough data yet — the season record has no replayed games to compare.
        </EmptyLine>
      </section>
    );
  }

  const agree   = segs.find((s) => s.state === "agree")!;
  const youOnly = segs.find((s) => s.state === "you-only")!;
  const ledgerBets = agree.count + youOnly.count;

  const skips = (summary.skipCodes ?? [])
    .filter((s) => s && typeof s.code === "string" && isNum(s.count) && s.count > 0)
    .sort((a, b) => b.count - a.count);

  const orphans = (summary.neverCommitted ?? []).filter((g) => g && g.game);

  const from = summary.window?.from ?? null;
  const to   = summary.window?.to ?? null;
  const windowGames = summary.window?.games;
  const games = isNum(windowGames) ? windowGames : total;

  const defaultContrast =
    "The performance panel counts a different population — every real-priced graded " +
    "STRONG row, including the ones before this window — which is why the two figures " +
    "do not add up to the same number.";

  return (
    <section className={styles.card}>
      <Head
        eyebrow="Replay vs ledger"
        title="Where today's model and your actual bets disagree"
      />

      <div
        className={styles.divBar}
        role="img"
        aria-label={segs
          .map((s) => `${s.label}: ${s.count} ${plural(s.count, "game")}`)
          .join("; ")}
      >
        {/* A zero-count category is dropped from the BAR -- min-width
            would otherwise draw it a 3px sliver, which reads as "a few"
            rather than "none". It still gets a row in the key below,
            printing 0, so nothing appears to vanish. */}
        {segs
          .filter((s) => s.count > 0)
          .map((s) => (
            <div
              key={s.state}
              className={styles.divSeg}
              data-seg={s.state}
              style={{ flexGrow: s.count, flexBasis: 0 }}
              title={`${s.label} — ${fmtInt(s.count)} ${plural(s.count, "game")}`}
            >
              {fmtInt(s.count)}
            </div>
          ))}
      </div>

      <div className={styles.divKey}>
        {segs.map((s) => (
          <div key={s.state} className={styles.divKeyRow}>
            <span className={styles.divKeySwatch} data-seg={s.state} aria-hidden />
            <span className={styles.divKeyLabel}>
              {s.label}
              {s.simulated && s.count > 0 && <span className="tag">Simulated</span>}
            </span>
            <span className={styles.divKeyN}>{fmtInt(s.count)}</span>
            <span
              className={styles.divKeyU}
              data-sim={s.simulated ? "1" : undefined}
              data-money={
                s.simulated || s.units == null
                  ? undefined
                  : s.units > 0.005
                    ? "up"
                    : s.units < -0.005
                      ? "down"
                      : "flat"
              }
            >
              {segReturnText(s.units, s.count)}
            </span>
          </div>
        ))}
      </div>

      <p className="copy">
        <b className={styles.fig}>{fmtInt(youOnly.count)}</b> of the{" "}
        <b className={styles.fig}>{fmtInt(ledgerBets)}</b> bets in your ledger are ones
        today&rsquo;s model would decline
        {isNum(youOnly.units) && (
          <>
            , and they returned{" "}
            <b className={styles.fig}>{segReturnText(youOnly.units, youOnly.count)}</b>{" "}
            per unit staked
          </>
        )}
        . The <b className={styles.fig}>{fmtInt(agree.count)}</b> it agrees with returned{" "}
        <b className={styles.fig}>{segReturnText(agree.units, agree.count)}</b>.
      </p>

      {skips.length > 0 && (
        <div className={styles.divCodes}>
          <span className={styles.divCodesHead}>Why the replay passed:</span>
          {skips.map((s) => (
            <span key={s.code} className={styles.divCode}>
              {skipWords(s.code, s.label)} <b>{fmtInt(s.count)}</b>
            </span>
          ))}
        </div>
      )}

      {orphans.length > 0 && (
        <div className={styles.divAlert}>
          <span className={styles.divAlertDot} aria-hidden />
          <span>
            <b>
              {fmtInt(orphans.length)} {plural(orphans.length, "game")} flagged STRONG and
              never carried a bet
            </b>{" "}
            — {orphans.map((g) => `${shortDate(g.date)} ${g.game}`).join(", ")}. Those are
            pipeline failures, not decisions.
          </span>
        </div>
      )}

      <p className={styles.foot}>
        Covers the {fmtInt(games)} {plural(games, "game")} in the season record
        {from && to ? <>, {shortDate(from)} to {shortDate(to)}</> : null}. {contrastNote ?? defaultContrast}
      </p>
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Shared card header.
   ═══════════════════════════════════════════════════════════════════ */

function Head({
  eyebrow,
  title,
  sub,
}: {
  eyebrow: string;
  title: string;
  sub?: string;
}) {
  return (
    <div className={styles.head}>
      <div className="eyebrow">{eyebrow}</div>
      <div className={styles.title}>
        {title}
        {sub && <span className={styles.titleSub}> · {sub}</span>}
      </div>
    </div>
  );
}
