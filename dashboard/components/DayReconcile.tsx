"use client";

/**
 * DayReconcile -- "THE NIGHT, GAME BY GAME".
 *
 * WHY THIS COMPONENT EXISTS (2026-07-28)
 * --------------------------------------
 * One night -- 2026-07-27 -- was described three different ways on one
 * screen: the ticker said "6 STRONG YRFI", the ledger card said "4
 * graded bets -0.33u", and the season-record strip said "1 bet". Every
 * one of those figures was arithmetically defensible and nothing on the
 * page said why they differed, so it read as "my bets are missing".
 *
 * This table is the answer. Every flagged game appears exactly ONCE, on
 * one line, with what YOU did sitting next to what the MODEL REPLAY
 * would have done and a plain-English reason for the difference. Games
 * are grouped by whether the two agreed, so the disagreements -- the
 * only rows worth arguing about -- are impossible to miss.
 *
 * THE COUNTS ARE NOT COMPUTED HERE. They arrive already computed from
 * lib/reconcile.ts and are rendered through chainText(), the same
 * string the ticker and the TONIGHT card use. Deriving them a second
 * time at a second call site is literally how the screen came to
 * describe one night three ways.
 *
 * TWO THINGS THIS FILE MUST NEVER DO:
 *   1. Render 0 -- or the words "no bet" -- for a date the replay has
 *      not covered. The replay only runs over graded days, so tonight
 *      legitimately has no replay row: it says "not replayed yet".
 *      "no bet" means the replay looked and passed. They are DIFFERENT
 *      STATEMENTS and swapping them invents a decision that never
 *      happened -- a fresh instance of the exact bug above.
 *   2. Tone-colour a replay figure. Peach and rust mean real money that
 *      moved. The replay compounds an imaginary bank; it renders in
 *      --muted-foreground whatever its sign.
 */

import { Fragment, useMemo } from "react";
import type { NightCounts } from "@/lib/reconcile";
import { chainText, fmtOdds, fmtU, nightFromRecord, replayText } from "@/lib/reconcile";
import type { RecDay, RecGame } from "@/lib/season-record";
import { isNum } from "@/lib/season-record";
import styles from "./DayReconcile.module.css";

export interface DayReconcileProps {
  /** The selected date's entry in season_record.json (REAL side). null
   *  for tonight, and for any date the record does not cover. */
  day: RecDay | null;
  /** The already-computed counts for the same date. RoiPanel resolves
   *  this as `nightFromRecord(day) ?? nightFromBoard(...)`, so a graded
   *  date and the live slate arrive in the same shape. */
  tonight: NightCounts | null;
  selectedDate: string;
  /** The record side's window, used only by the two out-of-range empty
   *  states so they can name the date instead of saying "out of range". */
  sideFrom?: string | null;
  sideTo?: string | null;
}

/* ── row state ─────────────────────────────────────────────────────── */

type RowState = "agree" | "you-only" | "replay-only" | "idle";

/** Order is fixed and meaningful: agreement first, then the two kinds
 *  of disagreement, then the games nobody touched. */
const GROUPS: { state: RowState; label: string }[] = [
  { state: "agree", label: "Both acted" },
  { state: "you-only", label: "You bet, replay passed" },
  { state: "replay-only", label: "Replay bet, you didn't" },
  { state: "idle", label: "Flagged, nobody bet" },
];

function rowState(g: RecGame): RowState {
  const placed = g.ledger?.placed === true;
  const replayBet = g.record?.action === "BET";
  if (placed && replayBet) return "agree";
  if (placed) return "you-only";
  if (replayBet) return "replay-only";
  return "idle";
}

/* ── small formatters ──────────────────────────────────────────────── */

const DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MON_UC = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const MON_TC = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-27" -> "MON JUL 27". Built in UTC on purpose: these are ET
 *  slate dates, not instants, and `new Date("2026-07-27")` parsed as
 *  local time slips to the 26th for anyone west of Greenwich. */
function longDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
  const dow = DOW[new Date(Date.UTC(y, mo - 1, d)).getUTCDay()];
  return `${dow} ${MON_UC[mo - 1]} ${d}`;
}

/** "2026-05-07" -> "May 7", for the empty states that name a date. */
function shortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${MON_TC[Number(m[2]) - 1]} ${Number(m[3])}`;
}

const plural = (n: number, word: string) => (n === 1 ? word : `${word}s`);

/** Drop the leading zero so probabilities read as probabilities:
 *  "0.418" -> ".418", "0.40" -> ".40". */
const trimZero = (s: string) => s.replace(/^0(?=\.)/, "");

/** Direction of a real-money figure. The dead band matches fmtU's, so a
 *  figure that prints "0.00u" never gets a colour that implies a win. */
function tone(n: number | null | undefined): "win" | "loss" | "none" {
  if (!isNum(n)) return "none";
  if (n > 0.005) return "win";
  if (n < -0.005) return "loss";
  return "none";
}

/* ── "why they differ" ─────────────────────────────────────────────── */

/** The exporter emits a stable machine `code` alongside its English
 *  `reason`. Switch on the code; the prose wording is free to change.
 *  Older exports predate `code`, so rather than show the operator
 *  nothing we substring-match the prose as a fallback. */
function codeFromReason(reason: string | undefined): string {
  const r = (reason || "").toLowerCase();
  if (r.includes("confident enough")) return "gate";
  if (r.includes("too few runs")) return "lambda_floor";
  if (r.includes("too many runs")) return "lambda_ceiling";
  if (r.includes("no edge")) return "kelly_no_edge";
  if (r.includes("risk cap") || r.includes("daily cap")) return "daily_cap";
  if (r.includes("price")) return "no_price";
  if (r.includes("history") || r.includes("unscored")) return "unscored";
  return "";
}

/** "model now reads .418 · needs ≤ .40".
 *
 *  The gate threshold is not carried on the per-game record, so we lift
 *  it out of the exporter's prose ("... 0.418 vs 0.40 needed"). If that
 *  ever stops parsing we drop the threshold rather than invent one --
 *  a hardcoded .40 would silently go stale the day the gate moves. */
function gateText(g: RecGame): string {
  const p = isNum(g.modelP) ? trimZero(g.modelP.toFixed(3)) : null;
  const m = /([0-9]*\.?[0-9]+)\s*needed/.exec(g.record?.reason || "");
  const need = m ? trimZero(m[1]) : null;
  // modelP is p(no run in the 1st): YRFI needs it LOW, NRFI needs it HIGH.
  const dir = g.side === "NRFI" ? "≥" : "≤";
  if (p && need) return `model now reads ${p} · needs ${dir} ${need}`;
  if (p) return `model now reads ${p} · not confident enough`;
  return "the model was not confident enough";
}

function whyText(g: RecGame): string {
  const r = g.record;
  if (!r) return "not replayed yet";
  if (r.action === "BET") return "both agree";

  switch (r.code || codeFromReason(r.reason)) {
    case "gate":           return gateText(g);
    case "lambda_floor":   return "λ below the YRFI run floor";
    case "lambda_ceiling": return "λ above the NRFI run ceiling";
    case "no_price":       return "no DK price captured — the replay can't size it";
    case "daily_cap":      return "the replay's daily risk budget was already spent";
    case "kelly_no_edge":  return "no edge at that price";
    case "unscored":       return "before the replay had enough history to score";
    // Unknown code: show the exporter's own words rather than a blank.
    default:               return r.reason || "the replay passed on this one";
  }
}

/* ── cells ─────────────────────────────────────────────────────────── */

/** Real money. Four renderings, deliberately distinct: a settled bet,
 *  a placed bet still waiting, a game we flagged and skipped, and a
 *  game the ledger never flagged at all. */
function YouCell({ g }: { g: RecGame }) {
  const l = g.ledger;

  // No ledger entry: the replay found this game on its own. A dash is
  // honest here -- there is nothing of ours to describe.
  if (!l) return <span className={styles.words}>—</span>;

  // Flagged but never bet. The words are SANS on purpose: a missing
  // monospace numeral is itself the cue that there is no number.
  if (!l.placed) return <span className={styles.words}>not placed</span>;

  const odds = fmtOdds(l.odds);

  if (isNum(l.pnl)) {
    const t = tone(l.pnl);
    return (
      <>
        <span className={styles.fig} data-tone={t}>{`${fmtU(l.pnl)} @ ${odds}`}</span>
        <span className={styles.outcome} data-tone={t}>
          {t === "win" ? "WIN" : t === "loss" ? "LOSS" : "PUSH"}
        </span>
      </>
    );
  }

  // Placed, not graded. The stake is not profit, so it carries no sign
  // and no direction colour -- only the amber PENDING marker.
  const risked = isNum(l.unitsRisked) ? `${l.unitsRisked.toFixed(2)}u` : "staked";
  return (
    <>
      <span className={styles.fig}>{`${risked} @ ${odds}`}</span>
      <span className={styles.outcome} data-tone="pending">PENDING</span>
    </>
  );
}

/** Simulated money. Never tone-coloured, always suffixed "sim". */
function ReplayCell({ g }: { g: RecGame }) {
  const r = g.record;

  // Absent record = this date was never replayed. NOT the same claim as
  // "the replay passed"; never swap these two strings.
  if (!r) return <span className={styles.words}>not replayed yet</span>;
  if (r.action !== "BET") return <span className={styles.words}>no bet</span>;

  const stake = isNum(r.stake) ? `${r.stake.toFixed(2)}u` : "staked";
  return (
    <>
      <span className={styles.fig} data-tone="sim">
        {stake}
        {r.assumed ? "*" : ""}
      </span>
      <span className={styles.simSuffix}>sim</span>
    </>
  );
}

function Row({ g, state }: { g: RecGame; state: RowState }) {
  // The whole point of the table: the live system put real money on a
  // game the current model would pass on.
  const disagrees = g.record?.action === "SKIP" && g.ledger?.placed === true;

  return (
    <div className={styles.row} data-state={state}>
      <div className={styles.cellGame}>{g.game}</div>
      <div className={styles.cellSide}>
        <span className={styles.pill} data-side={g.side}>{g.side}</span>
      </div>
      <div className={`${styles.cellP} ${styles.num}`}>
        {isNum(g.modelP)
          ? <span className={styles.fig}>{g.modelP.toFixed(3)}</span>
          : <span className={styles.words}>—</span>}
      </div>
      <div className={styles.cellYou}><YouCell g={g} /></div>
      <div className={styles.cellReplay}><ReplayCell g={g} /></div>
      <div className={styles.cellWhy}>
        <span>{whyText(g)}</span>
        {disagrees && <span className={styles.disagree}>MODEL DISAGREES</span>}
      </div>
    </div>
  );
}

/* ── the component ─────────────────────────────────────────────────── */

export function DayReconcile({
  day,
  tonight,
  selectedDate,
  sideFrom,
  sideTo,
}: DayReconcileProps) {
  // ONE source. `tonight` is whatever the parent already resolved; the
  // fallback only fires if a caller hands us a day without its counts.
  const counts: NightCounts | null = tonight ?? nightFromRecord(day);

  const groups = useMemo(() => {
    if (!day) return [];
    // Keep the original index: doubleheader legs collide on
    // (date, game, side), so the index is what makes the React key
    // unique -- and a bare array index alone reorders rows on refetch.
    const indexed = day.games.map((g, i) => ({ g, i, state: rowState(g) }));
    return GROUPS.map(({ state, label }) => ({
      state,
      label,
      rows: indexed
        .filter((r) => r.state === state)
        // Money-movers to the top of each group.
        .sort((a, b) =>
          Math.abs(b.g.ledger?.pnl ?? 0) - Math.abs(a.g.ledger?.pnl ?? 0)),
    })).filter((grp) => grp.rows.length > 0);
  }, [day]);

  if (!counts) return null;

  const date = counts.date || selectedDate;
  const hasRows = groups.some((grp) => grp.rows.length > 0);

  // Pulled out once, as plain locals: every downstream use is then a
  // straight number check instead of a chain of optional lookups that
  // could quietly read `undefined.toFixed`.
  const simPnl = day && isNum(day.simPnl) ? day.simPnl : null;
  const simBankAfter = isNum(counts.replayBank) ? counts.replayBank : null;

  /* ── header figures ───────────────────────────────────────────── */
  const youTone = tone(counts.ledgerPL);

  /* ── settled record ───────────────────────────────────────────── */
  let wins = 0, losses = 0, pushes = 0;
  for (const g of day?.games ?? []) {
    const pnl = g.ledger?.placed ? g.ledger.pnl : null;
    if (!isNum(pnl)) continue;
    if (pnl > 0.005) wins += 1;
    else if (pnl < -0.005) losses += 1;
    else pushes += 1;
  }

  /* ── footer line 1: you ───────────────────────────────────────── */
  const youFoot = [
    `${counts.placed} placed`,
    `${counts.settled} settled`,
    day && counts.settled > 0 ? `${wins}W-${losses}L` : null,
    pushes > 0 ? `${pushes} ${plural(pushes, "push")}` : null,
    fmtU(counts.ledgerPL),
  ].filter(Boolean).join(" · ");

  /* ── footer line 2: the replay ────────────────────────────────── */
  // NOTE the two replay P&L figures are DIFFERENT STAKE SCHEMES over
  // the same bets, and both are labelled: `flat` is one unit a bet
  // (NightCounts.replayPL), `compounding` is the quarter-Kelly run that
  // the bank arrow ties out to. Printing either one unlabelled is how
  // you end up with a fourth contradictory number for the night.
  let replayFoot: string;
  if (counts.replay == null) {
    replayFoot = "not replayed yet";
  } else {
    // Quarter-Kelly leads: that is the sizing the operator actually
    // stakes by now, so it is the figure the day should be read in.
    // Flat stays as the un-leveraged reference, labelled.
    const parts = [`staked ${counts.replay} of ${counts.flagged}`];
    if (simPnl != null) parts.push(`${fmtU(simPnl)} at ¼-Kelly`);
    if (isNum(counts.replayPL)) parts.push(`${fmtU(counts.replayPL)} at flat 1u`);
    if (simPnl != null && simBankAfter != null) {
      const before = simBankAfter - simPnl;
      parts.push(`bank ${before.toFixed(2)}u → ${simBankAfter.toFixed(2)}u sim`);
    }
    replayFoot = parts.join(" · ");
  }

  /* ── the reconciliation paragraph ─────────────────────────────── */
  const para: string[] = [];
  if (counts.placed === 0) {
    para.push("No real bets were placed on this date.");
  } else if (counts.settled === counts.placed) {
    para.push(
      `${counts.placed} ${plural(counts.placed, "bet")} ` +
      `${counts.placed === 1 ? "was" : "were"} placed at real prices and ` +
      `settled at ${fmtU(counts.ledgerPL)}.`,
    );
  } else {
    para.push(
      `${counts.placed} ${plural(counts.placed, "bet")} ` +
      `${counts.placed === 1 ? "was" : "were"} placed at real prices; ` +
      `${counts.settled} of them ${counts.settled === 1 ? "has" : "have"} ` +
      `settled, at ${fmtU(counts.ledgerPL)}.`,
    );
  }
  if (counts.replay == null) {
    para.push(
      "The model replay has not run for this date yet — it fills in " +
      "after the last first inning finishes.",
    );
  } else if (counts.replay === 0) {
    para.push(
      `The replay staked none of the ${counts.flagged} flagged ` +
      `${plural(counts.flagged, "game")}.`,
    );
  } else {
    const staked = (day?.games ?? []).reduce((sum, g) => {
      const st = g.record?.action === "BET" ? g.record.stake : undefined;
      return sum + (isNum(st) ? st : 0);
    }, 0);
    const outcome = simPnl == null
      ? ""
      : simPnl < -0.005 ? " and lost"
      : simPnl > 0.005 ? " and won"
      : " and broke even";
    para.push(
      `The replay staked ${counts.replay} of the ${counts.flagged} flagged ` +
      `${plural(counts.flagged, "game")} at a simulated ` +
      `${staked.toFixed(2)}u${outcome}.`,
    );
  }
  // The 100u bank is the exporter's fixed startBank. It is not passed
  // down here, and it has not moved since the replay was built.
  para.push(
    "They differ because the replay re-runs today's model with " +
    "compounding Kelly stakes on an imaginary 100u bank — different " +
    "games, different stake sizes, not money.",
  );

  /* ── empty state (no per-game rows to show) ───────────────────── */
  let emptyText: string | null = null;
  if (!hasRows) {
    if (sideFrom && date < sideFrom) {
      emptyText = `Before this record starts (${shortDate(sideFrom)}).`;
    } else if (counts.flagged > 0) {
      emptyText =
        "Tonight's games haven't graded yet. The replay column fills in " +
        "after the last first inning finishes.";
    } else if (sideTo && date > sideTo) {
      emptyText = `After this record's last graded day (${shortDate(sideTo)}).`;
    } else {
      emptyText = "The model flagged nothing on this date.";
    }
  }

  /* ── notes under the grid ─────────────────────────────────────── */
  const assumedPrice = (day?.games ?? []).some((g) => g.record?.assumed === true);
  // Season-wide this is 14 real / 35 projected bets sized to zero, and
  // it used to look identical to a night with no games at all.
  const cappedOut = (day?.games ?? []).filter(
    (g) => g.record?.action === "SKIP" && g.record.code === "daily_cap",
  ).length;
  const zeroStaked = day && counts.replay === 0 && cappedOut > 0
    ? `The model flagged ${counts.flagged} ${plural(counts.flagged, "game")} ` +
      `here, but the replay's Kelly sizing put ${cappedOut} of them at ` +
      `zero — the day's risk cap was already spent.`
    : null;

  // Spans, not divs: on a graded night this fragment is the content of a
  // <summary>, whose content model is phrasing content.
  const head = (
    <>
      <span className={styles.headLeft}>
        <span className="eyebrow">{longDate(date)}</span>
        {/* The one string. Same selector as the ticker and the TONIGHT
            card, so these three surfaces cannot drift apart. */}
        <span className={styles.chain}>{chainText(counts)}</span>
        {/* The replay is a SEPARATE population, on its own line. Put it
            on the arrow chain above and the reader subtracts
            6 - 4 - 4 - 1 and concludes bets went missing. */}
        <span className={styles.replayChain}>{replayText(counts)}</span>
      </span>
      <span className={styles.headTotals}>
        <span className="eyebrow">You</span>
        <span className={`figStat ${styles.headFig}`} data-tone={youTone}>
          {fmtU(counts.ledgerPL)}
        </span>
      </span>
    </>
  );

  const body = (
    <>
      {/* Column heads only exist to label rows; with no rows they are
          six empty boxes above an explanation. */}
      {hasRows && (
        <div className={styles.scroll}>
          <div className={styles.grid}>
            <div className={styles.colHead}>Game</div>
            <div className={styles.colHead}>Side</div>
            {/* "MODEL p", not "MODEL P" -- p is the probability symbol,
                so this one head opts out of the uppercase transform. */}
            <div className={`${styles.colHead} ${styles.colHeadRaw} ${styles.num}`}>
              MODEL p
            </div>
            <div className={styles.colHead}>You</div>
            <div className={styles.colHead}>Replay (sim)</div>
            <div className={styles.colHead}>Why they differ</div>

            {/* Fragments, not wrapper divs: every section head and every
                row has to be a direct child of .grid or the six-track
                template does not reach it. */}
            {groups.map((grp) => (
              <Fragment key={grp.state}>
                <div className={styles.section}>
                  <span className="eyebrow">{grp.label}</span>
                  <span className={styles.sectionCount}>({grp.rows.length})</span>
                </div>
                {grp.rows.map(({ g, i }) => (
                  <Row key={`${date}|${g.game}|${g.side}|${i}`} g={g} state={grp.state} />
                ))}
              </Fragment>
            ))}
          </div>
        </div>
      )}

      {emptyText && <p className={`copy ${styles.empty}`}>{emptyText}</p>}
      {zeroStaked && <p className={`copy ${styles.empty}`}>{zeroStaked}</p>}
      {assumedPrice && (
        <p className={`meta ${styles.assumedNote}`}>
          {"* price not captured; the replay assumed −125."}
        </p>
      )}

      <div className={styles.foot}>
        <span className={styles.footLine}>
          <b>You:</b> {youFoot}
        </span>
        <span className={styles.footLine}>
          <b>Replay:</b> {replayFoot}
        </span>
      </div>

      <p className={`copy ${styles.reconcileNote}`}>{para.join(" ")}</p>
    </>
  );

  // The live slate is always open -- it is the thing the operator came
  // to look at. A graded night is collapsible, but still OPEN by
  // default: this table is the answer to "where did my bets go", and an
  // answer behind a disclosure triangle is not an answer.
  if (day) {
    return (
      <details className={styles.card} open>
        <summary className={styles.head}>{head}</summary>
        {body}
      </details>
    );
  }
  return (
    <section className={styles.card}>
      <div className={styles.head}>{head}</div>
      {body}
    </section>
  );
}

export default DayReconcile;
