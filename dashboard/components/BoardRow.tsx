"use client";

import type { BoardRow, GameDetail, PickSide, PickStrength, PickThresholds } from "@/lib/types";
import { LambdaMeter } from "./LambdaMeter";
import { GameDetails } from "./GameDetails";
import styles from "./BoardRow.module.css";

function toneClass(side: PickSide, strength: PickStrength): string {
  if (side === "NRFI") return strength === "STRONG" ? "nrfiStrong" : "nrfiLean";
  if (side === "YRFI") return strength === "STRONG" ? "yrfiStrong" : "yrfiLean";
  return "passTone";
}

function pickLabelText(side: PickSide, strength: PickStrength): string {
  if (strength === "STARTER PENDING") return "STARTER PENDING";
  if (strength === "LINEUP PENDING")  return "LINEUP PENDING";
  if (strength === "LOW LAMBDA")      return "PASS · LOW λ";
  // T2.25: NO DATA shows as plain PASS on the chip; the reason
  // (rookie debut / insufficient stats) lives in the tooltip + the
  // expanded GameDetails panel so the chip stays clean while the
  // user can drill in for the why.
  if (side === "PASS") return "PASS";
  return `${strength} ${side}`;
}

/** Human-readable explanation for NO DATA picks: which pitcher's stats
 *  are unavailable + the most likely cause (rookie debut / call-up).
 *  Shown in the pickPill tooltip and surfaced again in GameDetails. */
function noDataReason(detail: GameDetail | undefined): string {
  if (!detail) return "Insufficient data — model declined to predict.";
  const issues: string[] = [];
  if (detail.away.pitcher.quality === "avg") {
    const name = detail.away.pitcher.name && detail.away.pitcher.name !== "TBD"
      ? `${detail.away.pitcher.name} (${detail.away.team})`
      : `${detail.away.team}'s starter`;
    issues.push(`${name} has insufficient MLB stats — likely a rookie debut, recent call-up, or limited sample`);
  }
  if (detail.home.pitcher.quality === "avg") {
    const name = detail.home.pitcher.name && detail.home.pitcher.name !== "TBD"
      ? `${detail.home.pitcher.name} (${detail.home.team})`
      : `${detail.home.team}'s starter`;
    issues.push(`${name} has insufficient MLB stats — likely a rookie debut, recent call-up, or limited sample`);
  }
  if (detail.away.offense.quality === "avg") {
    issues.push(`${detail.away.team} team batting data unavailable`);
  }
  if (detail.home.offense.quality === "avg") {
    issues.push(`${detail.home.team} team batting data unavailable`);
  }
  if (issues.length === 0) {
    return "Insufficient data — model declined to predict.";
  }
  return "PASS — no real prediction made because:\n• " + issues.join("\n• ");
}

/** Mirror of mlb_first_inning_predictor.classify_pick_lr -- computes the
 *  pick the model WOULD return given an NRFI probability + total lambda,
 *  ignoring the LINEUP PENDING / STARTER PENDING guards.  Used to surface
 *  a "tentative lean" chip while a row is in the pending state, so the
 *  user has SOMETHING to look at before lineups post.
 *
 *  T2.9: thresholds are now sourced from data/thresholds.json (written
 *  by the predictor on every run) via the BoardResponse.thresholds
 *  field.  Falls back to these defaults only when the response doesn't
 *  carry thresholds (older deploy / missing file).  This keeps the
 *  TS classifier and the Python classifier aligned without manual sync. */
const _DEFAULT_THRESHOLDS = {
  strongNrfiP:     0.56,
  leanNrfiP:       0.56,
  passLoP:         0.44,
  leanYrfiP:       0.44,
  lambdaYrfiFloor: 0.78,
} as const;
function classifyTentative(
  pNrfi:       number,
  lambdaTotal: number | null,
  th:          PickThresholds | undefined,
): { side: PickSide; strength: PickStrength } {
  const t = th ?? _DEFAULT_THRESHOLDS;
  if (pNrfi >= t.strongNrfiP) return { side: "NRFI", strength: "STRONG" };
  if (pNrfi >= t.leanNrfiP)   return { side: "NRFI", strength: "LEAN" };
  if (pNrfi >= t.passLoP)     return { side: "PASS", strength: "NO EDGE" };
  if (lambdaTotal != null && lambdaTotal < t.lambdaYrfiFloor) {
    return { side: "PASS", strength: "LOW LAMBDA" };
  }
  if (pNrfi >= t.leanYrfiP)   return { side: "YRFI", strength: "LEAN" };
  return { side: "YRFI", strength: "STRONG" };
}

// "2:10 PM ET" -> "2:10 PM"; missing/empty -> "—"
function formatGameTime(s: string): string {
  if (!s) return "—";
  return s.replace(/\s*ET\s*$/, "").trim();
}

/** True for non-numeric time placeholders the predictor emits when MLB
 *  hasn't published a real start time yet -- e.g. "After Game 1" for the
 *  back end of a traditional doubleheader.  These get a chip treatment
 *  (dashed muted pill, parallel to STARTER/LINEUP PENDING) instead of
 *  the regular mono time, so they (a) read as tentative not as a real
 *  time, and (b) fit in the narrow 82px time column without overflowing
 *  into the matchup cell. */
function isPlaceholderTime(s: string): boolean {
  if (!s || s === "—") return false;
  return !s.includes(":");
}

// Short sportsbook name for the live-odds chip ("DraftKings" -> "DK")
function shortBook(name: string): string {
  if (!name) return "";
  const n = name.trim().toLowerCase();
  if (n.startsWith("draftking"))   return "DK";
  if (n.startsWith("fanduel"))     return "FD";
  if (n.startsWith("betmgm"))      return "MGM";
  if (n.startsWith("caesars"))     return "CAE";
  if (n.startsWith("pinnacle"))    return "PIN";
  return name.slice(0, 3).toUpperCase();
}

/** Tooltip for the P(YRFI) cell -- gives the user a peek at WHY two
 *  games with similar YRFI% can land in different pick zones.  Combined
 *  λ is the model's raw expected first-inning runs; if it's below the
 *  0.78 floor the predictor demotes a would-be YRFI pick to PASS. */
function lambdaTooltip(row: BoardRow): string {
  const lam = row.lambda.toFixed(2);
  const yrfi = row.yrfiPct.toFixed(1);
  const floor = "0.78";
  const note =
    row.lambda < 0.78
      ? `\n• Below 0.78 floor → would-be YRFI demoted to PASS - LOW λ`
      : `\n• Above 0.78 floor → YRFI bets enabled when YRFI% high enough`;
  return `Combined λ ${lam} (expected total 1st-inning runs)\nP(YRFI) ${yrfi}%${note}`;
}

// Normalize a single American-odds string: ensure a leading +/-.  Empty
// input -> empty output (caller decides what to render).
function normalizeAmericanOdds(raw: string): string {
  const s = (raw || "").trim();
  if (!s) return "";
  return /^[+\-]/.test(s) ? s : (Number.parseFloat(s) > 0 ? `+${s}` : s);
}

// Pick the right odds (NRFI vs YRFI) for the picked side, normalize sign.
function oddsForPick(side: PickSide, nrfi: string, yrfi: string): string {
  return normalizeAmericanOdds(side === "NRFI" ? nrfi : side === "YRFI" ? yrfi : "");
}

export function BoardRowItem({
  row,
  detail,
  expanded,
  onToggle,
  thresholds,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
  expanded: boolean;
  onToggle: () => void;
  thresholds?: PickThresholds;
}) {
  const tone = toneClass(row.pickSide, row.pickStrength);

  return (
    <div className={`${styles.row} ${styles[tone]} ${expanded ? styles.open : ""}`}>
      <button
        type="button"
        className={styles.clickable}
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={`details-${row.away}-${row.home}`}
      >
        <span className={styles.spine} aria-hidden />

        <TimeCell value={formatGameTime(row.gameTimeEt || detail?.gameTimeEt || "")} />

        <span className={styles.matchup}>
          <span className={styles.team}>{row.away}</span>
          <span className={styles.at}>at</span>
          <span className={styles.team}>{row.home}</span>
          {row.doubleHeader && row.doubleHeader !== "N" && (
            <span className={styles.dhTag} title={`Doubleheader Game ${row.gameNumber}`}>
              DH-{row.gameNumber}
            </span>
          )}
          <DataQualityBadge detail={detail} />
        </span>

        <span className={styles.resultCell}>
          {detail?.gradedResult ? (
            <ResultBadge
              graded={detail.gradedResult}
              actual={detail.actualSide}
              awayRuns={detail.fiAwayRuns}
              homeRuns={detail.fiHomeRuns}
              totalRuns={detail.fiTotalRuns}
            />
          ) : (
            <span className={styles.resultPending}>—</span>
          )}
        </span>

        <span
          className={`num ${styles.lambdaVal} ${styles.right}`}
          title={lambdaTooltip(row)}
        >
          {(row.yrfiPct / 100).toFixed(2)}
        </span>

        <span className={styles.pickCell}>
          <span
            className={`${styles.pickPill} ${
              (row.pickStrength === "STARTER PENDING"
                || row.pickStrength === "LINEUP PENDING")
                ? styles.pickPillPending
                : ""
            } ${row.pickStrength === "LOW LAMBDA" ? styles.pickPillLowLambda : ""}`}
            title={
              row.pickStrength === "LOW LAMBDA"
                ? `Demoted from STRONG/LEAN YRFI: combined λ ${row.lambda.toFixed(2)} below the 0.78 floor (model expects too few total runs to bet YRFI confidently). Tested in backtest: floor adds ~+1.36u/season.`
                : row.pickStrength === "NO DATA"
                  ? noDataReason(detail)
                  : undefined
            }
          >
            <span className={styles.pickDot} aria-hidden />
            <span className={styles.pickLabel}>
              {pickLabelText(row.pickSide, row.pickStrength)}
            </span>
          </span>
          <TentativeChip row={row} detail={detail} thresholds={thresholds} />
        </span>

        <span className={styles.oddsCell}>
          <OddsChip row={row} detail={detail} />
        </span>

        <EdgeCell row={row} detail={detail} />

        <span className={styles.meterCell}>
          <LambdaMeter yrfiProb={row.yrfiPct / 100} compact />
        </span>

        <span className={`num ${styles.pct} ${styles.right}`}>
          <Bar pct={row.nrfiPct} tone="nrfi" />
          {row.nrfiPct.toFixed(1)}
        </span>

        <span className={`num ${styles.pct} ${styles.right}`}>
          <Bar pct={row.yrfiPct} tone="yrfi" />
          {row.yrfiPct.toFixed(1)}
        </span>

        <span
          className={styles.expander}
          aria-hidden
          title={expanded ? "Hide details" : "Show details"}
        >
          <span className={`${styles.caret} ${expanded ? styles.caretOpen : ""}`}>
            ⌄
          </span>
        </span>
      </button>

      {expanded && (
        <div id={`details-${row.away}-${row.home}`} className={styles.detailsWrap}>
          <GameDetails row={row} detail={detail} />
        </div>
      )}
    </div>
  );
}

/** Data-quality badge -- shown when ANY model input is using a fallback
 *  rather than real data.  Triggers:
 *    - pitcher quality "avg" (TBD pitcher, league-default fallback)
 *    - offense quality "avg" (no team batting data)
 *    - top3c source != "lineup" (team-fallback used because lineup not posted)
 *  When all inputs are real, the badge is hidden.  Hover reveals the
 *  specific gaps so the user can judge how much to trust the verdict. */
function DataQualityBadge({ detail }: { detail: GameDetail | undefined }) {
  if (!detail) return null;
  const issues: string[] = [];
  const ap = detail.away.pitcher.quality;
  const hp = detail.home.pitcher.quality;
  const ao = detail.away.offense.quality;
  const ho = detail.home.offense.quality;
  // Pitcher data quality
  if (ap === "avg") issues.push(`${detail.away.team} pitcher: TBD / league-avg fallback`);
  if (hp === "avg") issues.push(`${detail.home.team} pitcher: TBD / league-avg fallback`);
  // Offense data quality
  if (ao === "avg") issues.push(`${detail.away.team} offense: league-avg fallback`);
  if (ho === "avg") issues.push(`${detail.home.team} offense: league-avg fallback`);
  // Lineup data quality (top-3 batters)
  if (detail.away.lineup.length === 0) {
    issues.push(`${detail.away.team} lineup: not posted yet`);
  }
  if (detail.home.lineup.length === 0) {
    issues.push(`${detail.home.team} lineup: not posted yet`);
  }
  if (issues.length === 0) return null;
  // Severity: any pitcher fallback = "high"; offense or lineup-only = "med"
  const severity = ap === "avg" || hp === "avg" ? "high" : "med";
  return (
    <span
      className={`${styles.dqBadge} ${severity === "high" ? styles.dqHigh : styles.dqMed}`}
      title={`Data quality issues:\n• ${issues.join("\n• ")}`}
      aria-label={`Data quality: ${issues.length} issues`}
    >
      <span aria-hidden>!</span>
    </span>
  );
}


/** Tentative-lean chip — shown ONLY when the row is LINEUP PENDING (the
 *  starter is known but the lineup hasn't posted yet, so the model has
 *  ~75% real input and is using team-fallback for top-3 batter stats).
 *  We surface what the model WOULD pick under those imputed inputs so
 *  the user has something concrete to look at while waiting for lineups
 *  to post -- but visually mark it as tentative (smaller chip, dashed
 *  border, "→" prefix) so it doesn't read as a real bet recommendation.
 *
 *  Skipped for STARTER PENDING (where pitcher data is also fallback,
 *  so the tentative would be noise) and for any non-pending state. */
function TentativeChip({
  row,
  detail,
  thresholds,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
  thresholds?: PickThresholds;
}) {
  if (row.pickStrength !== "LINEUP PENDING") return null;
  const pNrfi  = row.nrfiPct / 100;
  const lambda = detail?.lambdaLrTotal ?? row.lambda;
  const t      = classifyTentative(pNrfi, lambda, thresholds);
  // No point showing "PASS - No edge" or "PASS - Low lambda" as the
  // tentative -- those are just less-pending versions of PASS, not
  // actionable.  Only show when the model is actually leaning a side.
  if (t.side === "PASS") return null;
  const label = `${t.strength} ${t.side}`;
  const sideClass = t.side === "NRFI"
    ? styles.tentativeNrfi
    : styles.tentativeYrfi;
  return (
    <span
      className={`${styles.tentativeChip} ${sideClass}`}
      title={
        `Model leaning ${label} based on team-fallback batter stats. ` +
        `Will commit (or override) once MLB posts the actual lineup.`
      }
    >
      <span aria-hidden>→</span>
      <span className={styles.tentativeChipLabel}>{label}</span>
    </span>
  );
}


/** Live-odds chip: sportsbook + price, in its own grid column (T2.18).
 *
 *  Tone class is driven by `row.pickSide`:
 *   • NRFI pick → `.oddsNrfi` (warm-brown, matches NRFI row tinting)
 *   • YRFI pick → `.oddsYrfi` (red, matches YRFI row tinting)
 *   • PASS pick → `.oddsPending` (desaturated muted)
 *
 *  Bet decision is layered separately via `.oddsSkipped` (dashed border)
 *  when bet_placed === "N" on an actionable pick — preserves "we picked
 *  this side but the edge wasn't enough" without losing the side color.
 *
 *  Side labels (`N` / `Y`) prefix each price so the user can see at a
 *  glance which way the line is.  PASS rows render BOTH sides
 *  (`DK  N -130 · Y +100`); NRFI/YRFI rows render just the picked
 *  side (`DK  N -135`). */
function OddsChip({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  if (!detail) return null;

  const nrfiPrice = normalizeAmericanOdds(detail.marketNrfiOdds);
  const yrfiPrice = normalizeAmericanOdds(detail.marketYrfiOdds);
  if (!nrfiPrice && !yrfiPrice) return null;

  const book = shortBook(detail.sportsbook);
  const ageStr = relAge(detail.oddsCapturedAt);
  const ageSuffix = ageStr ? `\nCaptured ${ageStr}` : "";
  const bet = detail.betPlaced;

  // Tone class from pickSide.  Note: pending strengths (LINEUP PENDING /
  // STARTER PENDING) get pickSide="PASS" from the predictor while we wait
  // on data, so they correctly fall into the .oddsPending bucket.
  const toneClass =
    row.pickSide === "NRFI" ? styles.oddsNrfi
    : row.pickSide === "YRFI" ? styles.oddsYrfi
    : styles.oddsPending;

  // Skipped-bet overlay: only meaningful for actionable NRFI/YRFI picks
  // (PASS rows aren't bets so the dashed border would just be visual noise).
  const skipClass =
    bet === "N" && row.pickSide !== "PASS" ? styles.oddsSkipped : "";

  // PASS branch: both sides as informational market data.
  if (row.pickSide === "PASS") {
    return (
      <span
        className={`${styles.oddsChip} ${toneClass}`}
        title={
          `Market on ${detail.sportsbook || "DK"}: `
          + (nrfiPrice ? `NRFI ${nrfiPrice}` : "NRFI —")
          + " · "
          + (yrfiPrice ? `YRFI ${yrfiPrice}` : "YRFI —")
          + "\nModel declined or pending; no bet."
          + ageSuffix
        }
      >
        {book && <span className={styles.oddsBook}>{book}</span>}
        {nrfiPrice && (
          <span className={styles.oddsPair}>
            <span className={styles.oddsSideLabel}>N</span>
            <span className={styles.oddsPrice}>{nrfiPrice}</span>
          </span>
        )}
        {nrfiPrice && yrfiPrice && <span className={styles.oddsSep}>·</span>}
        {yrfiPrice && (
          <span className={styles.oddsPair}>
            <span className={styles.oddsSideLabel}>Y</span>
            <span className={styles.oddsPrice}>{yrfiPrice}</span>
          </span>
        )}
      </span>
    );
  }

  // NRFI / YRFI active pick — single side, tone-colored.
  const price = row.pickSide === "NRFI" ? nrfiPrice : yrfiPrice;
  if (!price) return null;
  const sideLabel = row.pickSide === "NRFI" ? "N" : "Y";
  const edgePct = detail.edgeOnPick != null ? detail.edgeOnPick * 100 : null;
  const edgeStr = edgePct == null ? "" : (edgePct >= 0 ? `+${edgePct.toFixed(1)}%` : `${edgePct.toFixed(1)}%`);

  return (
    <span
      className={`${styles.oddsChip} ${toneClass} ${skipClass}`}
      title={
        (bet === "Y"
          ? `Bet placed: ${row.pickSide} @ ${price} (edge ${edgeStr})`
          : bet === "N"
            ? `Skipped: edge ${edgeStr || "below threshold"} on ${row.pickSide} @ ${price}`
            : `${row.pickSide} @ ${price}${edgeStr ? ` (edge ${edgeStr})` : ""}`)
        + ageSuffix
      }
    >
      {book && <span className={styles.oddsBook}>{book}</span>}
      <span className={styles.oddsPair}>
        <span className={styles.oddsSideLabel}>{sideLabel}</span>
        <span className={styles.oddsPrice}>{price}</span>
      </span>
    </span>
  );
}

/** Format an ISO timestamp as a relative age ("47 min ago", "14 h ago",
 *  "3 d ago").  Returns empty string if unparseable.  Used for odds
 *  capture freshness display. */
function relAge(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const ageSec = Math.max(0, (Date.now() - t) / 1000);
  if (ageSec < 60)    return `${Math.round(ageSec)}s ago`;
  if (ageSec < 3600)  return `${Math.round(ageSec / 60)} min ago`;
  if (ageSec < 86400) return `${Math.round(ageSec / 3600)} h ago`;
  return `${Math.round(ageSec / 86400)} d ago`;
}


/** Dedicated EDGE column.  Right-aligned percentage with sign-aware tint:
 *   positive (good bet) → warm-brown (--primary)
 *   negative (skip)     → muted-red
 *   no odds / PASS pick → em-dash, low contrast */
function EdgeCell({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  // PASS picks: no side to price an edge against.
  if (row.pickSide === "PASS" || !detail || detail.edgeOnPick == null) {
    return <span className={`${styles.edgeCell} ${styles.edgeNone}`}>—</span>;
  }
  const pct = detail.edgeOnPick * 100;
  const cls = pct > 0 ? styles.edgePos : pct < 0 ? styles.edgeNeg : "";
  const sign = pct >= 0 ? "+" : "";
  return (
    <span
      className={`${styles.edgeCell} ${cls}`}
      title={`Model edge over implied prob: ${sign}${pct.toFixed(2)}%`}
    >
      {sign}{pct.toFixed(1)}%
    </span>
  );
}


/** Time column renderer.  Real wall-clock times ("12:35 PM") use the
 *  monospace .rank treatment for a tabular feel down the column.  When
 *  MLB hasn't published a real start (DH-Y game-2 placeholder, TBD,
 *  etc.) the predictor emits a non-numeric string -- we render that as
 *  a small dashed chip so the column rhythm isn't broken AND the chip
 *  visually telegraphs "this is a tentative state, not a real time". */
function TimeCell({ value }: { value: string }) {
  if (isPlaceholderTime(value)) {
    // Display abbreviation: "After Game 1" -> "After G1" so the chip fits
    // inside the 82px time column at its uppercase 9.5px sans tracking.
    // The full phrase stays in the tooltip for clarity.
    const display = value.replace(/\bGame\s+(\d+)\b/i, "G$1");
    return (
      <span
        className={styles.rankTag}
        title={`${value} — start time pending until prior game finishes`}
      >
        <span className={styles.rankTagDot} aria-hidden />
        {display}
      </span>
    );
  }
  return <span className={`num ${styles.rank}`}>{value}</span>;
}


function Bar({ pct, tone }: { pct: number; tone: "nrfi" | "yrfi" }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <span className={`${styles.inlineBar} ${styles[tone]}`}>
      <span className={styles.inlineBarFill} style={{ width: `${p}%` }} />
    </span>
  );
}


function ResultBadge({
  graded,
  actual,
  awayRuns,
  homeRuns,
  totalRuns,
}: {
  graded:    NonNullable<GameDetail["gradedResult"]>;
  actual:    GameDetail["actualSide"];
  awayRuns:  number | null;
  homeRuns:  number | null;
  totalRuns: number | null;
}) {
  // Box-score format: away-home, e.g. "0-2".  Falls back to total runs when
  // we only have an aggregate (older grading data).
  const score =
    awayRuns != null && homeRuns != null
      ? `${awayRuns}-${homeRuns}`
      : totalRuns != null
        ? `${totalRuns}R`
        : "-";
  const totalText = totalRuns != null ? `${totalRuns}R` : "-";

  // T3.16: build verbose accessible labels so screen readers announce
  // the full outcome instead of just "0-2" or "W".
  const ariaScore = awayRuns != null && homeRuns != null
    ? `${awayRuns} runs away, ${homeRuns} runs home`
    : totalRuns != null
      ? `${totalRuns} total runs`
      : "score not yet recorded";
  // Postponed / suspended games: amber pause indicator
  if (graded === "POSTPONED" || graded === "SUSPENDED") {
    return (
      <span
        className={`${styles.resultBadge} ${styles.resultPP}`}
        title={graded}
        aria-label={`Game ${graded.toLowerCase()}, no result.`}
        role="status"
      >
        <span className={styles.resultGlyph}>PP</span>
      </span>
    );
  }
  // PASS picks (model said no edge) -- show the actual box score muted
  if (graded === "PASS") {
    return (
      <span
        className={`${styles.resultBadge} ${styles.resultPass}`}
        title={`PASS · 1st inning ${score} · ${actual ?? "-"} (${totalText})`}
        aria-label={`Pass: model declined. First inning final ${ariaScore}, actual side ${actual ?? "unknown"}.`}
        role="status"
      >
        <span className={styles.resultGlyph}>{score}</span>
      </span>
    );
  }
  // Real bet outcomes
  const isWin = graded === "WIN";
  const cls   = isWin ? styles.resultWin : styles.resultLoss;
  const glyph = isWin ? "W" : "L";
  const outcomeWord = isWin ? "Win" : "Loss";
  return (
    <span
      className={`${styles.resultBadge} ${cls}`}
      title={`${graded} · 1st inning ${score} · ${actual ?? "-"} (${totalText})`}
      aria-label={`${outcomeWord}. First inning ${ariaScore}, actual side ${actual ?? "unknown"}.`}
      role="status"
    >
      <span className={styles.resultGlyph}>{glyph}</span>
      <span className={styles.resultRuns}>{score}</span>
    </span>
  );
}
