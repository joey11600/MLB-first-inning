# Rollout plan — first-inning model v3 (pooled first-inning pitcher xwOBA + L2 0.50)

**Date:** 2026-08-22 (plan) · **Live since:** commit `f7952566`, pushed **2026-08-23 10:18 ET** (gate fix `1008367f`); the 08-22 slate was still picked by the previous weights, so `MODEL_UPDATED_FROM` = 2026-08-23 ·
**Revert point:** tag `pre-fixwoba-2026-08-22` / branch `backup/pre-fixwoba-2026-08-22`

This is the operating plan for the change, written for whoever picks it up —
including the operator. Plain language first, exact commands second.

---

## 0. The one rule: the ledger is continuous

The season's record and units are **real history**. The No.1 strategy stands at
**+66.2u at quarter-Kelly since May 26** (`tools/pl_calc.py`, dashboard
`/history`). The new model does **not** rewrite any of it. From Aug 22 its picks
simply **append** to the same ledger and the same series.

So the dashboard keeps counting from May 26 (`CURRENT_SYSTEM_FROM` unchanged) and
now *says* that the model was updated on Aug 22. A second read-out —
"since the Aug 22 update" — is added as a sub-total (see §3), so both numbers are
visible: the whole season, and the new model on its own.

What the backtest says to expect (out of sample, same accounting):
old model ≈ .60 hit / +6u at Kelly on a May-26 refit vs **new ≈ .69 hit / +41u**;
on the same nights **+29u** (90% range +6 to +52). Treat that as the *shape* of
the expectation, not a promise — the real number is whatever the ledger records.

---

## 1. What is already live (verified)

| piece | where | status |
|---|---|---|
| 20-feature weights, L2 0.50, CIR calibrator refit on 2024-26 | `data/lr_t1.json`, `lr_b1.json`, `calibration_v2.json` | live; remote confirmed `home_fi_xwoba` weight +0.0328 |
| the pooled first-inning pitcher input as a running-sums state | `fi_pitcher_pool.py`, `data/fi_pitcher_pool.json` (81 KB, as of 2026-08-22) | live; incremental == validated batch builder to 5e-5 |
| predictor wiring (feature lists, values, self-refresh, fail-open) | `mlb_first_inning_predictor.py` | live; vectors are 20 long; colder starter → higher P(run) |
| nightly pool advance | `daily.yml` grade job step "Advance first-inning pitcher pool" | live |
| model gate knows the new feature | `tools/model_gate.py` | live; local before/after: Brier better on 2024, 2025, 2026 — PASS |
| dashboard reason map | `dashboard/lib/pick-reasons.ts` | live |
| tests | `tests/test_fi_pitcher_pool.py` (+7) | 271 passing |
| backups | `data/*.bak-2026-08-22-pre-fixwoba`, tag + branch above | pushed |

**Expected and normal:** ~15–20% fewer gate bets than before. The stronger
regularization compresses the raw scores and the 0.42 cut is unchanged — that is
the behaviour that was validated, not a defect.

---

## 2. First live confirmation (today)

The first hourly predict after the push should change the probabilities of
today's **unlocked** games (locked bets are frozen by design). Check:

```bash
python tools/refit2026/no1_since_may26.py
```
(prints the real ledger series — the new nights append at the bottom of "by month")

and, for the input itself:

```bash
python fi_pitcher_pool.py --show 681517
```
which should print a pooled value and a league mean around 0.32, with the state
"as of" yesterday's date (Eastern).

---

## 3. Dashboard continuity -- DONE 2026-08-22

1. **Copy** on `/history` now states the continuity rule (done, `TopPickHistory.tsx`).
2. **Sub-total "Since the Aug 23 model update"** (done): `TopPickReport.sinceUpdate`
   — record, at-Kelly, flat, staked — same rule over nights ≥ `MODEL_UPDATED_FROM`
   (= 2026-08-23, the first slate the new weights priced; corrected 2026-08-23);
   rendered as its own figure block on `/history` ("Nothing settled yet…" until the
   first bet settles). Never blended into the season figure.
3. **Marker** on the cumulative-units chart (done): a dashed `--rule` vertical at
   the first settled night on/after 2026-08-23, with a footer note.
4. Deploy via the normal path only: commit → push → auto-deploy; verify the live
   page shows the new copy (`curl -sL https://nrfi-terminal.vercel.app/history | grep -c "Aug 22"`).

---

## 4. Daily operation of the new input

- The state advances **one day at a time** from Savant. Two paths keep it current:
  the nightly cron step (commits the file) and the predictor's own refresh at
  predict time when it is behind (bounded to 12 days; `FI_POOL_REFRESH=0`
  disables). "Yesterday" is computed in **Eastern time** on purpose — a UTC
  runner at 9pm ET must never ingest today's half-finished slate.
- **If Savant is down:** nothing breaks. The last good state is used; a
  starter's pooled value moves slowly. The cron step is soft-fail.
- **If the state file is missing or empty:** every starter gets the league mean
  and a WARNING is printed. The model still runs.
- **Health check:** `python fi_pitcher_pool.py --show <pid>` — "as of" should be
  yesterday. If it lags by more than ~3 days, run `python fi_pitcher_pool.py --update`
  and commit the file.

---

## 5. Monitoring and acceptance (first 30 days)

Watch weekly (`python tools/refit2026/no1_since_may26.py` + `tools/pl_calc.py`):

| signal | healthy | act if |
|---|---|---|
| No.1 record since Aug 23 | trending ≥ 62% over 30+ nights (validated ≈ 69–74%) | < 55% over 30 nights → investigate, consider revert |
| gate volume | ~80–85% of the old rate | < 60% or > 120% → check the pool state and the calibrator file |
| claimed vs actual on STRONG bets | overshoot ≤ ~5 points | overshoot > 10 points for 3 weeks → recalibrate (CIR, §6) |
| `calibration_drift_monitor` | no weekly alert (now deduped + logged, T8.40) | alert → follow its playbook |
| pool `as_of` | yesterday | lag > 3 days → §4 |
| stakes | within 10u/15u caps, `stake_drift` clean | any drift → stop, read `2026-07-28_money_path_audit` |

Nothing here auto-reverts. Revert is a human decision, and it is one commit (§7).

---

## 6. The two foot-guns -- CLOSED 2026-08-22 (same day)

- `recalibrate_v2.py` now imports the predictor's feature lists, supplies the
  pooled first-inning xwOBA per game, reads the `_ptfix` 2025 file, and fits
  **CIR** (the shipped shape). It is safe to run as a manual, OOS-checked
  recalibration again (see its playbook in `calibration_drift_monitor`).
- `two_stage_model.py --fi-xwoba --l2 0.5` reproduces the shipped fit (same
  20 names, fi_xwoba weight 0.0330 vs 0.0328) and **refuses** to write any other
  feature set to the production paths. This is the canonical path for the next
  refit. The weekly auto-refit is OFF; keep it off.

---

## 7. Revert (one commit, ~2 minutes)

```bash
git checkout pre-fixwoba-2026-08-22 -- data/lr_t1.json data/lr_b1.json data/calibration_v2.json mlb_first_inning_predictor.py dashboard/lib/pick-reasons.ts tools/model_gate.py
git commit -m "revert: first-inning model v3 -> pre-fixwoba-2026-08-22 [gate-override]"
git push origin claude/mlb-inning-run-predictor-QyazL
```
(The `.bak` files and `fi_pitcher_pool.py` can stay; unused code is harmless.
The loader's name/weights check means a half-revert cannot silently run.)

---

## 8. Follow-ups, in priority order

1. ~~Dashboard sub-total + marker~~ — done 2026-08-22.
2. ~~Retire or port the `recalibrate` action to CIR~~ — done 2026-08-22 (ported).
3. ~~Ledger columns~~ — done 2026-08-22: Supabase migration first (additive,
   nullable), then tracker schema (appended last, frozen with the bet), predictor,
   writer maps. Verify after the next predict tick: the CSV header ends with
   `…,sizing_prob,home_fi_xwoba,away_fi_xwoba` and Supabase rows carry values.
4. ~~Teach `two_stage_model.py` the feature~~ — done 2026-08-22 (`--fi-xwoba`, guard, L2 units).
5. **Line shopping** — LIVE 2026-08-23: `odds_diagnostic.yml` captures every
   book's first-inning AND F5 totals four times a day, mirrors each snapshot into
   Supabase `odds_multibook`, and the board's odds chip shows **"best -105 MGM"**
   when another book beats the ledger price for the same side (tooltip: best
   NRFI/YRFI, book count, age). Day one: best-vs-worst book = ~3 points of
   break-even on the first-inning total. The ledger's price basis is unchanged.
6. **F5 odds capture** — same workflow, same file (`market = totals_1st_5_innings`).
   After ~2 weeks of captures: measure the F5 market's AUC vs ours and the
   cross-book spread on the first-inning total.
7. **Credit plan (2026-08-23, later the same day)** — operator: *"a better way
   to save on credits"*. Window `65:50` (one price per game at the lock),
   every US book in the same call (`--ledger-book fanduel`), snapshot 1x/day,
   balance on the Ops Health card. ~315/day → ~75/day; the №1's ledger price
   is the same lock-cycle FanDuel price, and its "best price" is now the best
   at the lock. CHANGELOG 2026-08-23 "Odds credits".

---

## 9. What was tried and is closed (so nobody re-runs it)

~95 candidates under the full protocol (memory `statcast_20_factor_backlog`,
CHANGELOG 2026-08-20..22): every other pooled pitcher metric, the whole batter
side, platoon, interactions, team history, defense, speed, schedule, recency
weighting, park re-shrink, collinearity fix, legacy-model swap, level correction,
wind direction (three times). The first-inning signal is the pitcher; this
feature is the one that measures it.
