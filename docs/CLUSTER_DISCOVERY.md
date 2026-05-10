# Cluster discovery pipeline

The system has three coordinated tools for finding STRONG-bet feature
combinations that lose money, watching them in real-time, and
eventually skipping bet placement on confirmed bad clusters.

```
1. DISCOVER    →   2. MONITOR              →   3. DEMOTE
   cluster_       loss_cluster_                apply_cluster_
   discovery.py    monitor.py                  demotion.py
   (scan ledger,   (track defined cluster's    (skip bet placement
    surface         recent-N record;             on matching rows
    candidates)     fire Telegram if drift)      via JSON config)
```

**The pipeline is intentionally three-stage to prevent overfitting.**
A cluster that LOOKS bad over n=10 may revert to expectation over
n=20.  Every step gives the data more time to confirm or fade the
pattern before we change behavior.

## Stage 1: Discovery

`tools/cluster_discovery.py` scans the season ledger and prints
ranked candidate clusters at three feature resolutions:

1. **(side, prob_band)** -- coarsest, largest sample sizes.  Catches
   "all STRONG YRFI in deep band" type signals.
2. **(side, prob_band, lambda_band)** -- mid-resolution.
3. **(side, prob_band, lambda_band, pitcher_min_q)** -- finest, but
   with a tighter sample-size floor (n>=4) and hit-rate ceiling
   (<=30%) to avoid spurious tiny clusters.

It's read-only.  Never changes a CSV, never fires Telegram, never
demotes bets.

Runs automatically:
- Once per nightly grade cron (output goes to the workflow log).
- Re-run anytime: `python tools/cluster_discovery.py --since 2026-04-15`

When you see a candidate that looks like real signal (substantial
sample, hit rate well below model expectation, persistent across
weeks), proceed to Stage 2.

## Stage 2: Monitor

`tools/loss_cluster_monitor.py` defines a list of named clusters
(e.g. `yrfi_040_band`) and watches each one's recent-5 record.  When
a cluster's last 5 graded matches show ≥4 losses with ≤20% hit rate,
it fires a `loss_cluster_streak` Telegram alert.

Adding a new cluster:

1. Open `tools/loss_cluster_monitor.py`.
2. Append a dict to `CLUSTERS`:
   ```python
   {
       "id":           "yrfi_below_040",
       "label":        "STRONG YRFI · nrfi_p < 0.40",
       "description":  "Cluster discovery flagged 6W-11L (35%) on n=17.",
       "match":        lambda row: (row.get("pick_side","").upper() == "YRFI"
                                    and float(row.get("nrfi_prob") or 0.5) < 0.40),
       "min_losses":      5,
       "max_hit_rate":    0.30,
       "recent_n":        5,
       "recent_min_losses": 4,
       "recent_max_hit":  0.20,
   }
   ```
3. Commit + push.  The next grade cron picks it up.

The Telegram alert body lists the recent trail and the documented
action plan (manual judgment skip OR `recalibrate_v2.py` on trailing
30-60 days).

## Stage 3: Demotion (auto-skip)

`tools/apply_cluster_demotion.py` reads `data/cluster_demotions.json`
and sets `bet_placed='N' + units_risked=''` on every ungraded STRONG
row matching an active demotion.  **It does NOT change pick_side /
pick_strength / pick_label** -- the model's verdict stays visible on
the dashboard for transparency; only the money commit is suppressed.

Operator workflow:

1. Loss cluster monitor fires Telegram for cluster X.
2. (Optional but recommended) backtest the proposed demotion against
   the trailing 30 days to confirm net P&L benefit.  Currently this
   is a manual exercise -- compare the cluster's actual P&L against
   what skipping would have saved.
3. Add an entry to `data/cluster_demotions.json`:
   ```json
   {
     "id": "yrfi_below_040",
     "reason": "Confirmed by monitor on YYYY-MM-DD; -6.0u over 17 bets.",
     "side": "YRFI",
     "nrfi_prob": { "min": null, "max": 0.40 },
     "active": true
   }
   ```
4. Commit + push.  Next predict cron tick auto-demotes any matching
   row's `bet_placed` to `N`.
5. **Reversible**: set `"active": false` (or remove the entry) and
   bets resume on the next predict cron.

Idempotent: re-running with no new matches is a no-op.

## Safety rules baked in

- **Stage 3 only touches ungraded rows.**  Already-resolved bets
  (W/L/PASS/POSTPONED) are never modified -- you cannot retroactively
  un-bet a settled play.
- **PASS rows are never demoted** (no bet to skip).
- Every demotion writes a `pick_changes.csv` journal entry naming
  the cluster id + reason, so the audit trail stays intact.
- Each step is reversible: removing a demotion entry and re-running
  the predict cron lets future bets resume.
- The monitor's recent-N threshold (4-of-5 losses, ≤20% hit) is
  deliberately strict so single bad days don't trip alerts.

## When to back off

If a confirmed cluster starts hitting again (e.g. wins 4 of next 5),
back off:
- Set the demotion's `active` to `false`.
- Re-evaluate the cluster definition.  The pattern may have shifted
  or been a transient calibration drift that resolved.

The right long-run fix for systematic miscalibration is a new
calibrator (`recalibrate_v2.py` on the most recent 30-60 days), not
a permanent demotion entry.  Demotions are a *tactical* layer; the
calibrator is the *strategic* one.

## Related files

- `tools/cluster_discovery.py` -- discovery scanner.
- `tools/loss_cluster_monitor.py` -- runtime monitor + Telegram.
- `tools/apply_cluster_demotion.py` -- the demotion applier.
- `data/cluster_demotions.json` -- operator-maintained demotions list.
- `data/pick_changes.csv` -- audit trail of every demotion event.
- `memory/loss_cluster_yrfi_040_band.md` -- the original watch entry
  that motivated this pipeline.
