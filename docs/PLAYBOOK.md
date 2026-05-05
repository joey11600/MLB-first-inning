# Diagnostic Playbook

This is the standard checklist when something looks wrong with the model
or with placed bets.  Built after the 2026-05-03 disaster (lost –4.56u
in one day; took 4 hours of manual investigation to find the cause).
The goal of every entry: **route directly to the diagnostic tool that
gives you a definitive answer in under 5 minutes**.

---

## 1. "We just had a –3u or worse day on STRONG bets"

The bleed pattern that kicked off this whole effort.  Run in this order:

### Step 1.1 — read the per-pick reasoning log (T4.6)

```bash
jq '.picks[] | select(.graded_result == "LOSS") | {matchup, warnings}' \
   data/diagnostics/picks/<DATE>.json
```

Each loss has a `warnings` array.  If a warning exists for the loss,
that's the answer.  Common patterns:

- **`"raw cache xera=14.71 is outside [2.0, 7.0]"`**
  -> A pitcher's small-sample 2026 cache value was extreme.  T4.2
  priors-pooling should be tame this; if not, the priors JSON is stale.
  Check `data/v2_perfect_2026/truepit_priors_per_pitcher_per_date.json`
  freshness.

- **`"calibrator flat zone: N bins all map to rate X"`**
  -> Calibrator is collapsing distinct raw inputs into the same output,
  causing correlated picks (the bin-collapse pathology).  Multiple
  picks with the same calibrated probability is the smell.

- **`"feature foo=X is z=Y sigma from training mean"`**
  -> A feature value is way outside training distribution.  Either the
  feature pipeline produced a bug, or the live game context is genuinely
  unusual.  Compare to the `priors_vs_raw` block in the same pick to see
  if T4.2 already shrunk it.

- **No warnings** -> picks were within normal feature ranges.  This is
  variance.  Do NOT change anything; the model lost a coin flip.

### Step 1.2 — read the shadow comparison (T4.4)

```bash
cat data/diagnostics/shadow_<DATE>.csv
```

This shows what V2 + T4.2 priors-pooling **would** have predicted.
If V2 fired STRONG and T4.2-shadow PASSed, T4.2 disagreed -- and the
fact that T4.2 isn't yet selecting differently in production means the
priors JSON either isn't fresh OR a pitcher_q tag override prevented
shrinkage.

### Step 1.3 — check the drift monitor (T4.5)

```bash
cat data/diagnostics/drift_<DATE>.csv | grep HIGH
```

If anything fires HIGH severity, it explains the day.  Common entries:

- `home_pitcher_q=live shift=+30%` -> a code change re-tagged pitchers
  as 'live', disabling protective ERA-blend (= the T2.53 regression).
- `home_xera_extreme: 4/15 picks have xera > 7.0` -> small-sample
  noise leaking through; investigate priors-refresh status.
- `pick_cluster_size: 5` -> calibrator bin collapse.

### Step 1.4 — git blame the predictor

If steps 1-3 don't surface anything, look at recent code changes:

```bash
git log --since="3 days ago" --oneline -- \
    mlb_first_inning_predictor.py \
    tools/build_truepit_2026_with_priors.py \
    data/calibration_v2.json data/lr_t1.json data/lr_b1.json
```

Anything touching the feature pipeline in the last 24-48h is suspect.

---

## 2. "Telegram fired a HIGH drift alert"

The drift monitor (`tools/feature_drift_monitor.py`) fires on:
- `pitcher_q` tag distribution shift >= 30pp (the T2.53 fingerprint)
- numeric feature mean shift >= 3 sigma
- numeric feature stdev shift >= 3 sigma
- pick clustering >= 4 picks at same calibrated probability
- extreme value count fraction >= 30%

Action sequence:

### Step 2.1 — read the report

```bash
cat data/diagnostics/drift_<DATE>.csv
```

The `note` column tells you exactly which metric flipped.  Anything
related to `pitcher_q` is the **highest priority** -- it's the same
class of failure as T2.53.

### Step 2.2 — confirm with shadow + reasoning log

If `pitcher_q` distribution changed, the per-pick log will show
the new tag values directly (`pitcher_q.home_pitcher_q`), and the
shadow report will show how many picks T4.2 would have PASSed on.

### Step 2.3 — decide: pause bets or proceed?

If 4+ HIGH alerts AND any of them are about `pitcher_q` or `xera`
distribution, **immediately raise `--min-edge` to 0.99 in daily.yml
(the halt mechanism)** and investigate.  See history of T4.3 emergency
halt for example commit message.

If 1-2 LOW/MEDIUM alerts only, monitor.  Lineup-source alerts are
auto-downgraded but still appear; ignore those unless they coincide
with other reds.

---

## 3. "Daily shadow delta has been negative for 5+ consecutive days"

The auto-shadow report (`tools/daily_shadow_report.py`) appends
one row per day to `data/diagnostics/shadow_summary.csv`.  If
`delta_pl` (T4.2 minus V2) goes consistently negative, T4.2 is no
longer providing the protective shrinkage.

```bash
tail -10 data/diagnostics/shadow_summary.csv
```

Possible causes:

- **Priors JSON is stale**: the daily 6 UTC refresh failed or hasn't
  been running.  Check
  `git log --oneline -- data/v2_perfect_2026/truepit_priors_per_pitcher_per_date.json`.
  Should have a commit per day.
- **2025 priors aggregates are stale**: extremely unlikely (file
  doesn't change), but worth confirming
  `data/v2_perfect_2026/2025_priors_aggregates.json` exists and has
  >= 100 pitchers.
- **pitcher_q tag re-broke**: re-introducing the T2.53-class regression.
  Check the feature drift monitor output for the same window.
- **MLB drift**: legitimate league-wide change in NRFI rates.  Out of
  this playbook's scope -- needs a model rebuild.

---

## 4. "I'm about to merge a PR that touches the predictor"

The shadow gate (`.github/workflows/shadow_gate.yml`, T4.7) will run
automatically.  It posts a comment with V2 actual vs V2+T4.2-shadow
P&L over the trailing 14 days, using the PR's code path.

- If `delta_pl` is positive or near-zero, the PR is safe to merge from
  a model-quality standpoint.
- If `delta_pl < -2.0u`, the gate fails and the PR comment shows a
  red header.  Do NOT override unless you understand exactly why the
  shadow regressed and accept the trade-off.

The trigger paths are listed at the top of `shadow_gate.yml`.  If
your PR doesn't touch any of them, the gate doesn't fire, but you
still might want to run it manually:

```bash
python tools/v2_t42_shadow.py --days 14 --output-json /tmp/shadow.json
```

---

## 5. "Live state isn't updating in the dashboard"

This is operational, not model.  Quick checks:

### Step 5.1 — Railway worker

```bash
# Are the recent deploy logs clean?
# Browser to https://railway.com/project/.../service/.../deployments
# Look for "[live_state] connected" + "pushed N/M games" lines.
# 401 errors = SUPABASE_SERVICE_KEY is invalid; rotate the key.
```

### Step 5.2 — GH Actions fallback

The 5-min odds-only cron also runs `python workers/live_state.py --once`
as a safety net.  Check the most recent Actions run for that step.

### Step 5.3 — Supabase

```sql
SELECT MAX(updated_at), COUNT(*)
FROM live_game_state
WHERE date = CURRENT_DATE;
```

If updated_at is fresh (<2 minutes ago), the issue is dashboard-side.
If updated_at is stale, the worker isn't writing.

---

## Quick reference: the diagnostic stack

| layer | tool | output | runs |
|---|---|---|---|
| Detection | `tools/feature_drift_monitor.py` | `data/diagnostics/drift_<date>.csv` | nightly grade cron |
| Detection | `tools/daily_shadow_report.py` | `data/diagnostics/shadow_<date>.csv` + summary | nightly grade cron |
| Investigation | `tools/pick_reasoning_log.py` | `data/diagnostics/picks/<date>.json` | nightly grade cron |
| Investigation | `tools/v2_t42_shadow.py` | stdout, optional JSON | manual / PR gate |
| Process gate | `.github/workflows/shadow_gate.yml` | PR comment + status check | every PR |
| Investigation | `tools/multi_variant_3fold.py` | stdout | manual (rebuild research) |

## When in doubt

If none of these checks surface a clear answer, the most likely thing
is **variance**.  A 60% true-rate model has a ~5% probability of going
2-6 over 8 STRONG bets.  Don't rebuild the model on a single bad day.
Wait 7-14 days of data, re-run the multi-variant 3-fold backtest, and
make changes only if the longer-window evidence shows real regression.

The lesson from this week: **diagnostics first, rebuild last**.  The
T2.53 fix was 50 lines of code; we almost spent a week rewriting the
whole architecture before we noticed the data-layer regression.
