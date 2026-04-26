"use client";

import type { BoardRow, DataQuality, GameDetail, OffenseStats, PitcherStats } from "@/lib/types";
import { LambdaMeter } from "./LambdaMeter";
import styles from "./GameDetails.module.css";

export function GameDetails({ row, detail }: { row: BoardRow; detail: GameDetail | undefined }) {
  const hasDetail = Boolean(detail);

  return (
    <div className={styles.wrap}>
      <div className={styles.topGrid}>
        <div className={styles.projCol}>
          <div className="eyebrow">Projection</div>
          <div className={styles.projTable}>
            <ProjRow label="AWAY bats (top 1st)" value={detail?.awayProj ?? null} />
            <ProjRow label="HOME bats (bot 1st)" value={detail?.homeProj ?? null} />
            <ProjRow label="Combined λ" value={detail?.combinedLambda ?? row.lambda} highlight />
          </div>
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

      <div className={styles.matchupGrid}>
        <TeamCard
          side="AWAY"
          team={row.away}
          pitcher={detail?.away.pitcher}
          offense={detail?.away.offense}
        />
        <div className={styles.vsCol}>
          <span className={styles.vs}>VS</span>
          <div className={styles.vsLine} />
        </div>
        <TeamCard
          side="HOME"
          team={row.home}
          pitcher={detail?.home.pitcher}
          offense={detail?.home.offense}
        />
      </div>
    </div>
  );
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
  pitcher,
  offense,
}: {
  side: "AWAY" | "HOME";
  team: string;
  pitcher: PitcherStats | undefined;
  offense: OffenseStats | undefined;
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

      {pitcher?.fiIp !== null && pitcher?.fiIp !== undefined && pitcher.fiIp > 0 && (
        <>
          <section className={styles.subHead}>
            <span className="eyebrow">First-inning split</span>
            <span className={styles.fiIp}>{pitcher.fiIp.toFixed(1)} IP</span>
          </section>
          <StatGrid
            stats={[
              { k: "FI·ERA", v: pitcher.fiEra },
              { k: "FI·WHIP", v: pitcher.fiWhip, digits: 2 },
            ]}
            half
          />
        </>
      )}

      <section className={styles.subHead}>
        <span className="eyebrow">Offense</span>
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
