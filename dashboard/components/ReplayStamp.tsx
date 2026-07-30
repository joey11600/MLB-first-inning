import styles from "./ReplayStamp.module.css";

/* ============================================================
   PROVENANCE STAMP for every replay-driven figure on /history.

   WHY IT EXISTS. Operator, 2026-07-30: *"why did the profit change
   again from yesterday?"* Nothing was wrong — but three different sets
   of numbers appeared on this page in one day and nothing on screen
   said why:

     1. the unit re-basing changed what a "u" means in the Day column
     2. the STRONG gate moved 0.40 -> 0.42
     3. the nightly replay rebuild will re-simulate the WHOLE season
        under the new gate, moving every historical row

   (3) is the surprising one and it is inherent, not a bug. These charts
   answer "what would today's system have done all season", so changing
   today's system rewrites the history by design. A chart that silently
   self-rewrites is indistinguishable from a broken one, and the
   operator has been burned by figures moving under them before.

   So each replay card now carries the gate and the build time that
   produced it. Compare two screenshots and the difference is visible
   instead of mysterious.

   THE STALENESS LINE IS THE PART THAT EARNS ITS KEEP. `thresholds.json`
   holds the LIVE gate and `season_record.json` holds the one the replay
   was built at. When they disagree the numbers on screen are already
   known to be superseded, and we can say so BEFORE the rebuild rather
   than let the operator discover it afterwards. That is exactly the
   state this component shipped in: live 0.42, replay built at 0.40.

   NOT ON THE #1 PICK CARD, deliberately. That one reads the ledger, so
   it can only move when a real bet settles. Stamping it would imply a
   volatility it does not have.
   ============================================================ */

export interface ReplayStampProps {
  /** `gates.yrfi` from season_record.json — what the replay was run at. */
  recordGate: number | null;
  /** `generatedUtc` from season_record.json. */
  generatedUtc: string | null;
  /** `strongYrfiP` from thresholds.json — what the predictor runs NOW. */
  liveGate: number | null;
}

/** "2026-07-30T06:07:54Z" -> "30 Jul 06:07 UTC". UTC is stated rather
 *  than converted: the rebuild is driven by a cron on UTC, so a local
 *  rendering would make "built this morning" ambiguous by a day near
 *  midnight. */
function stampTime(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const d = new Date(t);
  const day = d.getUTCDate();
  const mon = d.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day} ${mon} ${hh}:${mm} UTC`;
}

export function ReplayStamp({
  recordGate, generatedUtc, liveGate,
}: ReplayStampProps) {
  if (recordGate == null && generatedUtc == null) return null;

  // A gap of a whole basis point or more. Floating-point equality on a
  // number that arrives from two different JSON files is not safe.
  const stale =
    recordGate != null && liveGate != null &&
    Math.abs(recordGate - liveGate) > 0.0001;

  return (
    <div className={styles.wrap}>
      <span className={styles.line}>
        Replay
        {recordGate != null && <> · gate {recordGate.toFixed(2)}</>}
        {generatedUtc && <> · built {stampTime(generatedUtc)}</>}
      </span>
      {stale && liveGate != null && (
        /* --attn, because this is the one line here that is asking the
           operator to expect something rather than just describing. */
        <span className={styles.stale}>
          <span className={styles.dot} aria-hidden />
          The live gate is now {liveGate.toFixed(2)}. These figures were
          replayed at {recordGate!.toFixed(2)}, so every row will move at
          the next nightly rebuild — no real bet changes, the simulation
          just re-runs under the new rule.
        </span>
      )}
    </div>
  );
}
