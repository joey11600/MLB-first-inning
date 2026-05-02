"use client";

import type {
  BatterLine,
  BoardRow,
  DataQuality,
  GameDetail,
  OffenseStats,
  PitcherStats,
  PickSide,
  PickStrength,
} from "@/lib/types";
import { LambdaMeter } from "./LambdaMeter";
import styles from "./GameDetails.module.css";

export function GameDetails({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  const hasDetail = Boolean(detail);

  return (
    <div className={styles.wrap}>
      <div className={styles.topGrid}>
        <div className={styles.projCol}>
          <ProjectionPanel row={row} detail={detail} />
          <div className={styles.meterBig}>
            <LambdaMeter yrfiProb={row.yrfiPct / 100} />
          </div>
        </div>

        <div className={styles.probCol}>
          <div className="eyebrow">Probabilities</div>
          <ProbBar label="NRFI (0 runs)" pct={row.nrfiPct} tone="nrfi" />
          <ProbBar label="YRFI (1+ runs)" pct={row.yrfiPct} tone="yrfi" />
          {detail?.overProb !== null && detail?.overProb !== undefined && (
            <ProbBar label="Over 1.5 runs" pct={detail.overProb * 100} tone="neutral" />
          )}
          {detail?.underProb !== null && detail?.underProb !== undefined && (
            <ProbBar label="Under 1.5 runs" pct={detail.underProb * 100} tone="neutral" />
          )}
        </div>

        <div className={styles.contextCol}>
          <div className="eyebrow">Context</div>
          <div className={styles.kvTable}>
            <KV k="Game time" v={detail?.gameTimeEt || "—"} mono />
            <KV
              k="Park factor"
              v={detail?.parkFactor ? detail.parkFactor.toFixed(3) : "—"}
              mono
            />
            <KV
              k="Blended inputs"
              v={
                detail?.blendedInputs !== null && detail?.blendedInputs !== undefined
                  ? `${detail.blendedInputs}/4`
                  : "—"
              }
              mono
            />
            <KV k="Pick" v={row.pickLabel} />
          </div>
        </div>
      </div>

      {!hasDetail && (
        <div className={styles.notice}>
          No per-game stat log found for this date. Run{" "}
          <code>python mlb_first_inning_predictor.py --date MM/DD/YYYY</code>{" "}
          to generate the picks CSV with full stat context.
        </div>
      )}

      {detail?.gradedResult && (
        <ResultBanner
          row={row}
          detail={detail}
        />
      )}

      <WhyThisPickPanel detail={detail} pickSide={row.pickSide} />

      <div className={styles.matchupGrid}>
        <TeamCard
          side="AWAY"
          team={row.away}
          oppTeam={row.home}
          pitcher={detail?.away.pitcher}
          offense={detail?.away.offense}
          /* Away pitcher works the bottom of the 1st against the home lineup */
          oppLineup={detail?.home.lineup}
          oppLineupQuality={detail?.home.offense.quality}
        />
        <div className={styles.vsCol}>
          <span className={styles.vs}>VS</span>
          <div className={styles.vsLine} />
        </div>
        <TeamCard
          side="HOME"
          team={row.home}
          oppTeam={row.away}
          pitcher={detail?.home.pitcher}
          offense={detail?.home.offense}
          /* Home pitcher works the top of the 1st against the away lineup */
          oppLineup={detail?.away.lineup}
          oppLineupQuality={detail?.away.offense.quality}
        />
      </div>
    </div>
  );
}

/** T4.15: "Why this pick?" panel.  Surfaces the top-5 LR feature
 *  contributions per half so the user can see the model's reasoning,
 *  not just the verdict.  Each row: feature name + raw value + a
 *  signed bar showing magnitude/direction.  Positive contribution
 *  pushes toward NRFI, negative toward YRFI.  Hidden when no
 *  contributions are persisted (older rows / pre-LR-v4). */
function WhyThisPickPanel({
  detail,
  pickSide,
}: {
  detail: GameDetail | undefined;
  pickSide: PickSide;
}) {
  const t1 = detail?.topFactorsT1 ?? [];
  const b1 = detail?.topFactorsB1 ?? [];
  if (t1.length === 0 && b1.length === 0) return null;
  // Combine the two halves and sort by absolute contribution; cap at 8
  // so the panel stays scannable.  Tag each with which half it came
  // from for the user's reference.
  const combined = [
    ...t1.map(f => ({ ...f, half: "T1" as const })),
    ...b1.map(f => ({ ...f, half: "B1" as const })),
  ].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)).slice(0, 8);
  const maxAbs = combined.reduce(
    (m, f) => Math.max(m, Math.abs(f.contribution)),
    0.001,
  );

  return (
    <section className={styles.whyPanel}>
      <div className={styles.whyHead}>
        <span className="eyebrow">Why this pick</span>
        <span className={styles.whySub}>
          Top features driving the model's verdict
        </span>
      </div>
      <ul className={styles.whyList}>
        {combined.map((f, i) => {
          const pct = Math.min(100, Math.abs(f.contribution) / maxAbs * 100);
          const dir: "nrfi" | "yrfi" = f.contribution > 0 ? "nrfi" : "yrfi";
          return (
            <li key={`${f.half}-${f.name}-${i}`} className={styles.whyRow}>
              <span className={styles.whyHalf} title={f.half === "T1" ? "Top of 1st" : "Bottom of 1st"}>
                {f.half}
              </span>
              <span className={styles.whyName} title={featureTooltip(f.name)}>{prettyFeatureName(f.name)}</span>
              <span className={`num ${styles.whyValue}`} title={`raw value`}>
                {fmtFeatureVal(f.name, f.value)}
              </span>
              <span className={styles.whyBarTrack}>
                <span
                  className={`${styles.whyBarFill} ${styles[dir === "nrfi" ? "whyNrfi" : "whyYrfi"]}`}
                  style={{ width: `${pct}%` }}
                  aria-hidden
                />
              </span>
              <span
                className={`num ${styles.whyContrib}`}
                title={`Pushes toward ${dir.toUpperCase()} by ${Math.abs(f.contribution).toFixed(3)} log-odds`}
              >
                {f.contribution > 0 ? "→ N" : "→ Y"} {Math.abs(f.contribution).toFixed(2)}
              </span>
            </li>
          );
        })}
      </ul>
      <div className={styles.whyFoot}>
        Pick: <strong>{pickSide}</strong> · Bars show signed log-odds contribution; longer bar = stronger push toward that side.
      </div>
    </section>
  );
}

/** Short, human-readable feature label.  Maps the snake_case keys from
 *  _T1/B1_EXPECTED_FEATURES to phrasing the user can scan quickly. */
function prettyFeatureName(name: string): string {
  const map: Record<string, string> = {
    fi_park_nrfi_rate:           "Park 1st-inning NRFI rate",
    home_fip:                    "Home pitcher FIP",
    away_fip:                    "Away pitcher FIP",
    home_obp:                    "Home OBP (top 9)",
    away_obp:                    "Away OBP (top 9)",
    wx_temp_c:                   "Temperature",
    wx_wind_kmh:                 "Wind speed",
    wx_humidity:                 "Humidity",
    wx_is_dome:                  "Dome (weather neutralized)",
    home_p_last5_pitcher_nrfi:   "Home SP last-5 NRFI rate",
    away_p_last5_pitcher_nrfi:   "Away SP last-5 NRFI rate",
    home_p_last10_pitcher_nrfi:  "Home SP last-10 NRFI rate",
    away_p_last10_pitcher_nrfi:  "Away SP last-10 NRFI rate",
    home_top3c_obp:              "Home top-3 OBP",
    away_top3c_obp:              "Away top-3 OBP",
    home_top3c_slg:              "Home top-3 SLG",
    away_top3c_slg:              "Away top-3 SLG",
    home_top3c_iso:              "Home top-3 ISO",
    away_top3c_iso:              "Away top-3 ISO",
    home_plate_ump_nrfi_rate:    "Home-plate ump NRFI rate",
    home_xera:                   "Home pitcher xERA (Statcast)",
    away_xera:                   "Away pitcher xERA (Statcast)",
    home_whiff_pct_rank:         "Home whiff-rate rank",
    away_whiff_pct_rank:         "Away whiff-rate rank",
    era_gap_t1:                  "ERA gap (T1)",
    era_gap_b1:                  "ERA gap (B1)",
    home_pvt_nrfi_rate:          "Home SP vs this opp (career)",
    away_pvt_nrfi_rate:          "Away SP vs this opp (career)",
    home_avg_ip_per_start:       "Home SP avg IP/start",
    away_avg_ip_per_start:       "Away SP avg IP/start",
  };
  return map[name] ?? name;
}

/** Format a feature value with the right precision for its scale. */
function fmtFeatureVal(name: string, v: number): string {
  if (name.includes("rank")) return v.toFixed(0);
  if (name === "wx_temp_c" || name === "wx_humidity" || name === "wx_wind_kmh")
    return v.toFixed(1);
  if (name === "wx_is_dome") return v ? "yes" : "no";
  if (name.includes("ip_per_start")) return v.toFixed(1);
  return v.toFixed(3);
}

/** Hover-tooltip body for each feature.  Native `title=""` attribute --
 *  shows on hover (desktop) and long-press (mobile), no JS needed.
 *  Goal: explain what the stat IS so someone unfamiliar with sabermetrics
 *  can read the rationale at a glance.  Especially important for xERA
 *  (Statcast expected ERA) since the lowercase "x" is easy to miss next
 *  to the player card's raw ERA. */
function featureTooltip(name: string): string {
  const map: Record<string, string> = {
    fi_park_nrfi_rate:
      "Park 1st-inning NRFI rate. % of games at this stadium with no run in the 1st (3-yr rolling). League avg ≈ 53%.",
    home_fip:
      "Home pitcher FIP. Fielding-Independent Pitching: ERA estimate from Ks/walks/HRs only, strips out defense + luck. League avg ≈ 4.20.",
    away_fip:
      "Away pitcher FIP. Fielding-Independent Pitching: ERA estimate from Ks/walks/HRs only, strips out defense + luck. League avg ≈ 4.20.",
    home_obp:
      "Home team's full lineup on-base %. Batters reach base this often.",
    away_obp:
      "Away team's full lineup on-base %. Batters reach base this often.",
    wx_temp_c:
      "First-pitch temperature in °C. Hotter air = ball flies further = more runs.",
    wx_wind_kmh:
      "Wind speed in km/h. Higher absolute speed = more variance in batted-ball distance.",
    wx_humidity:
      "Relative humidity %. Damp air is denser, suppresses fly-ball carry.",
    wx_is_dome:
      "Indoor stadium — weather inputs neutralized.",
    home_p_last5_pitcher_nrfi:
      "Home SP last-5 starts NRFI rate. Recent form: % of last 5 starts where pitcher's first inning was scoreless.",
    away_p_last5_pitcher_nrfi:
      "Away SP last-5 starts NRFI rate. Recent form: % of last 5 starts where pitcher's first inning was scoreless.",
    home_p_last10_pitcher_nrfi:
      "Home SP last-10 starts NRFI rate. Same as last-5 but smoother window.",
    away_p_last10_pitcher_nrfi:
      "Away SP last-10 starts NRFI rate. Same as last-5 but smoother window.",
    home_top3c_obp:
      "Home top-3 hitters' combined OBP. The 1-2-3 batters' on-base rate; first inning is almost always these 3 hitters.",
    away_top3c_obp:
      "Away top-3 hitters' combined OBP. The 1-2-3 batters' on-base rate; first inning is almost always these 3 hitters.",
    home_top3c_slg:
      "Home top-3 hitters' combined SLG (slugging %). Power output of the 1-2-3 hitters.",
    away_top3c_slg:
      "Away top-3 hitters' combined SLG (slugging %). Power output of the 1-2-3 hitters.",
    home_top3c_iso:
      "Home top-3 hitters' combined ISO (isolated power = SLG − AVG). Pure extra-base hit rate; runs above singles.",
    away_top3c_iso:
      "Away top-3 hitters' combined ISO (isolated power = SLG − AVG). Pure extra-base hit rate; runs above singles.",
    home_plate_ump_nrfi_rate:
      "Home-plate ump's career 1st-inning NRFI rate. Big strike zones produce more 1-2-3 frames.",
    home_xera:
      "Home pitcher xERA — Statcast 'expected ERA'. What his ERA SHOULD be based on quality-of-contact (exit velocity + launch angle), stripping out batted-ball luck. Often very different from raw ERA. League avg ≈ 4.20.",
    away_xera:
      "Away pitcher xERA — Statcast 'expected ERA'. What his ERA SHOULD be based on quality-of-contact (exit velocity + launch angle), stripping out batted-ball luck. Often very different from raw ERA. League avg ≈ 4.20.",
    home_whiff_pct_rank:
      "Home pitcher whiff-rate percentile rank (0-100). 100 = swinging-strike king; high = strikeout stuff = runs suppressed.",
    away_whiff_pct_rank:
      "Away pitcher whiff-rate percentile rank (0-100). 100 = swinging-strike king; high = strikeout stuff = runs suppressed.",
    era_gap_t1:
      "Signed ERA gap for top of 1st = home_era − away_era. Positive = home pitcher worse than away. Encodes 'worse pitcher gives up the run.'",
    era_gap_b1:
      "Signed ERA gap for bottom of 1st = away_era − home_era. Mirror of T1.",
    home_pvt_nrfi_rate:
      "Home SP vs this opponent (career) NRFI rate. Long-run head-to-head 1st-inning history.",
    away_pvt_nrfi_rate:
      "Away SP vs this opponent (career) NRFI rate. Long-run head-to-head 1st-inning history.",
    home_avg_ip_per_start:
      "Home SP average innings pitched per start. Proxy for stuff + efficiency.",
    away_avg_ip_per_start:
      "Away SP average innings pitched per start. Proxy for stuff + efficiency.",
  };
  return map[name] ?? `Model feature: ${name}`;
}


function ResultBanner({
  row,
  detail,
}: {
  row: BoardRow;
  detail: GameDetail;
}) {
  const graded = detail.gradedResult;
  const actual = detail.actualSide;
  const away   = detail.fiAwayRuns;
  const home   = detail.fiHomeRuns;
  const total  = detail.fiTotalRuns;

  // Postponed / suspended -- render a quiet pause notice
  if (graded === "POSTPONED" || graded === "SUSPENDED") {
    return (
      <div className={`${styles.resultBanner} ${styles.resultBannerPP}`}>
        <div className={styles.resultBannerLeft}>
          <span className="eyebrow">1st-inning result</span>
          <div className={styles.resultBannerScoreEmpty}>NOT PLAYED</div>
        </div>
        <div className={styles.resultBannerMid}>
          <span className={styles.resultBannerOutcome}>{graded}</span>
          <span className={styles.resultBannerSub}>
            Game did not play; not counted as a bet.
          </span>
        </div>
      </div>
    );
  }

  const totalText = total != null ? `${total} run${total === 1 ? "" : "s"}` : "—";
  const isWin = graded === "WIN";
  const isPass = graded === "PASS";

  const containerCls = isPass
    ? styles.resultBannerPass
    : isWin
    ? styles.resultBannerWin
    : styles.resultBannerLoss;

  const tone = isPass ? "pass" : isWin ? "win" : "loss";
  const outcomeLabel = isPass ? "PASS" : isWin ? "WIN" : "LOSS";
  const subText = isPass
    ? "Model said no edge — no bet placed."
    : `${row.pickLabel} → ${actual ?? "—"}`;

  return (
    <div className={`${styles.resultBanner} ${containerCls}`}>
      <div className={styles.resultBannerLeft}>
        <span className="eyebrow">1st-inning result</span>

        <div className={styles.scoreboard}>
          <TeamScore side="AWAY" team={row.away} runs={away} />
          <span className={styles.scoreSep}>–</span>
          <TeamScore side="HOME" team={row.home} runs={home} />
        </div>

        <div className={styles.scoreboardFoot}>
          <span>Total: <strong>{totalText}</strong></span>
          <span className={styles.scoreboardFootSep}>·</span>
          <span>Actual side: <strong>{actual ?? "—"}</strong></span>
        </div>
      </div>

      <div className={styles.resultBannerMid}>
        <span className={styles.resultBannerOutcome} data-tone={tone}>
          {outcomeLabel}
        </span>
        <span className={styles.resultBannerSub}>{subText}</span>
      </div>
    </div>
  );
}

function TeamScore({
  side,
  team,
  runs,
}: {
  side: "AWAY" | "HOME";
  team: string;
  runs: number | null | undefined;
}) {
  const hasData = runs !== null && runs !== undefined;
  const scored = hasData && (runs as number) > 0;
  const shutout = hasData && (runs as number) === 0;

  const cls = [
    styles.teamPanel,
    scored ? styles.teamPanelScored : "",
    shutout ? styles.teamPanelShutout : "",
  ]
    .filter(Boolean)
    .join(" ");

  const stateLabel = scored ? "SCORED" : shutout ? "SHUT OUT" : "—";

  return (
    <div className={cls}>
      <span className={styles.teamPanelLabel}>{side} · {stateLabel}</span>
      <span className={styles.teamPanelTeam}>{team}</span>
      <span className={styles.teamPanelRuns}>
        <span className={styles.teamPanelRunsNum}>
          {hasData ? runs : "—"}
        </span>
        <span className={styles.teamPanelRunsLabel}>
          {(runs as number) === 1 ? "RUN" : "RUNS"}
        </span>
      </span>
    </div>
  );
}

function ProjRow({
  label,
  value,
  highlight,
}: {
  label: string;
  value: number | null;
  highlight?: boolean;
}) {
  return (
    <div className={`${styles.projRow} ${highlight ? styles.projRowHl : ""}`}>
      <span className={styles.projLabel}>{label}</span>
      <span className={`num ${styles.projVal}`}>{value !== null ? value.toFixed(3) : "—"}</span>
    </div>
  );
}

/**
 * Slate-projections-style headline panel, embedded inside the row dropdown.
 * Replaces the previous standalone "SLATE PROJECTIONS" section.
 *
 * Shows the model's LR-v3 derived expected first-inning runs:
 *   - Combined total (big, color-coded by zone)
 *   - 10-bar visualization (each bar = 0.1 expected runs)
 *   - Zone badge (STRONG NRFI / PASS / STRONG YRFI)
 *   - Top-1st (home pitcher's half) + Bot-1st (away pitcher's half) breakdown
 */
function ProjectionPanel({
  row,
  detail,
}: {
  row: BoardRow;
  detail: GameDetail | undefined;
}) {
  const total =
    detail?.lambdaLrTotal ?? detail?.combinedLambda ?? row.lambda ?? null;
  const t1 = detail?.lambdaLrT1 ?? detail?.homeProj ?? null;
  const b1 = detail?.lambdaLrB1 ?? detail?.awayProj ?? null;
  const zone = rowZone(row.pickSide, row.pickStrength);
  const tone = totalTone(total);
  const filled = total != null ? Math.max(0, Math.min(10, Math.round(total * 10))) : 0;

  return (
    <div className={styles.projHero} data-zone={zone}>
      <div className={styles.projHeroHead}>
        <span className="eyebrow">First-inning projection</span>
        <ZoneBadge zone={zone} />
      </div>

      <div className={styles.projHeroBody}>
        <div className={styles.projHeroNum} data-tone={tone}>
          {fmtProj(total)}
          <span className={styles.projHeroNumUnit}>exp. runs</span>
        </div>
        <div className={styles.projBars} aria-hidden="true">
          {Array.from({ length: 10 }).map((_, i) => (
            <span
              key={i}
              className={styles.projBar}
              data-filled={i < filled ? "true" : "false"}
              data-tone={tone}
            />
          ))}
        </div>
      </div>

      <div className={styles.projSplit}>
        <div className={styles.projSplitCell}>
          <span className={styles.projSplitKey}>
            <span className={styles.projSplitDot} data-half="t1" /> Top 1st
          </span>
          <span className={`num ${styles.projSplitVal}`}>{fmtProj(t1)}</span>
          <span className={styles.projSplitMeta}>{detail?.home?.pitcher?.name?.trim() || "TBD"}</span>
        </div>
        <div className={styles.projSplitCell}>
          <span className={styles.projSplitKey}>
            <span className={styles.projSplitDot} data-half="b1" /> Bot 1st
          </span>
          <span className={`num ${styles.projSplitVal}`}>{fmtProj(b1)}</span>
          <span className={styles.projSplitMeta}>{detail?.away?.pitcher?.name?.trim() || "TBD"}</span>
        </div>
      </div>
    </div>
  );
}

function ZoneBadge({ zone }: { zone: "NRFI_STRONG" | "PASS" | "YRFI_STRONG" }) {
  if (zone === "NRFI_STRONG") {
    return <span className={styles.projZoneBadge} data-tone="green">Strong NRFI</span>;
  }
  if (zone === "YRFI_STRONG") {
    return <span className={styles.projZoneBadge} data-tone="red">Strong YRFI</span>;
  }
  return <span className={styles.projZoneBadge} data-tone="muted">Pass</span>;
}

function rowZone(side: PickSide, strength: PickStrength): "NRFI_STRONG" | "PASS" | "YRFI_STRONG" {
  if (side === "NRFI" && strength === "STRONG") return "NRFI_STRONG";
  if (side === "YRFI" && strength === "STRONG") return "YRFI_STRONG";
  return "PASS";
}

function totalTone(n: number | null): "green" | "lean_green" | "muted" | "lean_red" | "red" {
  if (n == null) return "muted";
  if (n <= 0.55) return "green";
  if (n <= 0.75) return "lean_green";
  if (n <  0.95) return "muted";
  if (n <  1.05) return "lean_red";
  return "red";
}

function fmtProj(n: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

function ProbBar({
  label,
  pct,
  tone,
}: {
  label: string;
  pct: number;
  tone: "nrfi" | "yrfi" | "neutral";
}) {
  return (
    <div className={styles.probRow}>
      <div className={styles.probHead}>
        <span className={styles.probLabel}>{label}</span>
        <span className={`num ${styles.probVal}`}>{pct.toFixed(1)}%</span>
      </div>
      <div className={styles.probTrack}>
        <div
          className={`${styles.probFill} ${styles[tone]}`}
          style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
        />
      </div>
    </div>
  );
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className={styles.kvRow}>
      <span className={styles.kvKey}>{k}</span>
      <span className={`${styles.kvVal} ${mono ? "num" : ""}`}>{v}</span>
    </div>
  );
}

function TeamCard({
  side,
  team,
  oppTeam,
  pitcher,
  offense,
  oppLineup,
  oppLineupQuality,
}: {
  side: "AWAY" | "HOME";
  team: string;
  /** Opposing team's three-letter code -- shown as "Vs {oppTeam} top of order" */
  oppTeam: string;
  pitcher: PitcherStats | undefined;
  offense: OffenseStats | undefined;
  /** The lineup that THIS team's pitcher will face (i.e. the OPPOSING team's
   *  top-3 batters).  Empty array when the lineup hasn't posted yet. */
  oppLineup: BatterLine[] | undefined;
  /** Data-quality tag for the opposing batting stats (live/ltd/sm/avg). */
  oppLineupQuality: DataQuality | undefined;
}) {
  return (
    <div className={styles.teamCard}>
      <header className={styles.teamHead}>
        <span className={styles.teamSide}>{side}</span>
        <span className={styles.teamCode}>{team}</span>
      </header>

      <section className={styles.subHead}>
        <span className="eyebrow">Pitcher</span>
        <QualityTag q={pitcher?.quality} />
      </section>
      <div className={styles.pitcherRow}>
        <PitcherAvatar
          mlbId={pitcher?.mlbId ?? null}
          name={pitcher?.name}
        />
        <div className={styles.pitcherName}>
          {pitcher?.name ?? "—"}
        </div>
      </div>
      <StatGrid
        stats={[
          { k: "ERA", v: pitcher?.era },
          { k: "WHIP", v: pitcher?.whip, digits: 2 },
          { k: "FIP", v: pitcher?.fip },
          { k: "K/9", v: pitcher?.k9, digits: 1 },
          { k: "BB/9", v: pitcher?.bb9, digits: 1 },
          { k: "HR/9", v: pitcher?.hr9, digits: 1 },
        ]}
      />

      <LineupPanel oppTeam={oppTeam} lineup={oppLineup} quality={oppLineupQuality} />

      <section className={styles.subHead}>
        <span className="eyebrow">Offense ({team})</span>
        <QualityTag q={offense?.quality} />
      </section>
      <StatGrid
        stats={[
          { k: "OBP", v: offense?.obp, digits: 3 },
          { k: "SLG", v: offense?.slg, digits: 3 },
          { k: "RPG", v: offense?.rpg, digits: 2 },
        ]}
      />
    </div>
  );
}


/**
 * Top-of-order matchup card -- shows the 3 batters this side's pitcher
 * will face in the half-inning we're projecting.  When MLB hasn't published
 * the lineup yet, render a muted "Lineup pending" placeholder so the user
 * knows the slot exists but is waiting on data (mirrors the PASS - LINEUP
 * PENDING pick state on the row).
 */
function LineupPanel({
  oppTeam,
  lineup,
  quality,
}: {
  oppTeam: string;
  lineup: BatterLine[] | undefined;
  quality: DataQuality | undefined;
}) {
  const has = Array.isArray(lineup) && lineup.length > 0;
  return (
    <>
      <section className={styles.subHead}>
        <span className="eyebrow">Vs {oppTeam} top of order</span>
        <QualityTag q={quality} />
      </section>
      {has ? (
        <ol className={styles.lineupList}>
          {lineup!.map((b, i) => (
            <BatterRow key={b.id || i} order={i + 1} batter={b} />
          ))}
        </ol>
      ) : (
        <div className={styles.lineupPending}>
          <span className={styles.lineupPendingDot} aria-hidden />
          Lineup pending — published 2–4 hours before first pitch
        </div>
      )}
    </>
  );
}


function BatterRow({ order, batter }: { order: number; batter: BatterLine }) {
  const obp = batter.obp != null ? batter.obp.toFixed(3) : "—";
  const slg = batter.slg != null ? batter.slg.toFixed(3) : "—";
  const iso = batter.iso != null ? batter.iso.toFixed(3) : "—";
  const ab  = batter.ab  != null ? `${batter.ab} AB` : "—";
  const handTag = batter.bats === "L" ? "LHB"
                : batter.bats === "R" ? "RHB"
                : batter.bats === "S" ? "SHB"
                : "";

  return (
    <li className={styles.lineupRow}>
      <span className={styles.lineupOrder}>{order}</span>
      <span className={styles.lineupName}>
        {batter.name || "—"}
        {handTag && (
          <span className={styles.lineupHand} data-hand={batter.bats}>{handTag}</span>
        )}
      </span>
      <span className={styles.lineupStats}>
        <span className={`num ${styles.lineupStat}`} title="OBP"><em>OBP</em>{obp}</span>
        <span className={`num ${styles.lineupStat}`} title="SLG"><em>SLG</em>{slg}</span>
        <span className={`num ${styles.lineupStat}`} title="ISO"><em>ISO</em>{iso}</span>
      </span>
      <span className={styles.lineupAb} title="AB this season">{ab}</span>
    </li>
  );
}

function StatGrid({
  stats,
  half,
}: {
  stats: { k: string; v: number | null | undefined; digits?: number }[];
  half?: boolean;
}) {
  return (
    <div className={`${styles.statGrid} ${half ? styles.statGridHalf : ""}`}>
      {stats.map((s) => (
        <div key={s.k} className={styles.stat}>
          <div className={styles.statKey}>{s.k}</div>
          <div className={`num ${styles.statVal}`}>
            {s.v !== null && s.v !== undefined ? s.v.toFixed(s.digits ?? 2) : "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

function QualityTag({ q }: { q: DataQuality | undefined }) {
  const label = q || "—";
  return (
    <span className={styles.qTag} data-q={q || "avg"} title={`Data quality: ${label}`}>
      {label}
    </span>
  );
}

function PitcherAvatar({
  mlbId,
  name,
}: {
  mlbId: number | null;
  name: string | undefined;
}) {
  if (!mlbId) {
    const fallback = (name || "").trim().toUpperCase() === "TBD"
      ? "TBD"
      : initialsFromName(name);
    return <div className={styles.pitcherAvatarFallback}>{fallback || "—"}</div>;
  }

  const url = `https://midfield.mlbstatic.com/v1/people/${mlbId}/spots/120`;
  return (
    <div className={styles.pitcherAvatar}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={name ? `${name} headshot` : "Pitcher headshot"} loading="lazy" />
    </div>
  );
}

function initialsFromName(name: string | undefined): string {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "—";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
