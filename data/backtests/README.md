# Backtest file variants — read the suffix carefully

The suffixes are NOT self-explanatory and one of them is actively
misleading. Measured on disk 2026-08-04 (AUDIT T8.1).

| suffix | ERA point-in-time? | what it is |
|---|---|---|
| `truepit` | ❌ **no** (0% within-season variation) | the original file |
| `truepit_pit` | ❌ **no** (0%) | `backfill_pit_pitching_stats.py` — pitching-stat backfill. **`_pit` does NOT mean point-in-time.** |
| `truepit_ptfix` | ✅ yes (73.3% / 77.5%) | **the point-in-time repair** |
| `truepit_pit_ptfix` | ✅ yes (73.3% / 77.5%) | both backfills |

**Use a `_ptfix` file for anything that trains or validates.** `_pit`
reads as "point-in-time" and is not; it has already sent two separate
analyses auditing a still-leaked file.

## The verdict columns are stale in ALL 2024/2025 variants

`lambda_total`, `nrfi_prob`, `yrfi_prob`, `pick_side` and
`pick_strength` were written by the **retired Poisson model** and were
never rebuilt — not even by `_ptfix`:

- `nrfi_prob == nrfi_prob_raw` in 100% of rows (the calibrator was never
  applied)
- `nrfi_prob == exp(-lambda_total)` in 100% of rows (max dev 7e-05)
- `lambda_total` is byte-identical between `_pit` and `_ptfix`

Their measured signal is a coin flip: `lambda_total` scores AUC 0.5008
(2024) / 0.4866 (2025), against 0.0535 directional strength for
`combined_lambda` on the live 2026 ledger.

**Never read these columns as "what the model would have done."** They
are what the *old* model did, on leaked inputs. Re-score from the
feature columns instead.

The FEATURE columns are fine — `fi_park_nrfi_rate` discriminates
normally in every file, which proves the rows are correctly aligned.
`two_stage_model.py` reads none of the verdict columns, so training and
live betting are unaffected.
