# NRFI Terminal — Upgrade Roadmap

Forward-looking list of system upgrades, ranked by **impact × effort**.
Companion to:
- `CHANGELOG.md` — what shipped, when, why (history)
- `AUDIT.md` — known bugs / fragility / hygiene items
- `docs/KB.md` — current system architecture

**Convention:** every meaningful fix or addition gets:
1. A new entry in `CHANGELOG.md` (dated, with audit ID `T<x.y>`)
2. The matching item below checked off `[x]` with a date + commit SHA
3. The architecture in `docs/KB.md` updated if the data flow changes

---

## ✅ Recently shipped (May 2026)

| Date | Ref | Item |
|---|---|---|
| 2026-05-02 | `2de0c3a` | **T2.29** — Rebalance board grid so PASS-row OddsChip stops clipping |
| 2026-05-02 | `bae3f34` | **T2.30 / Phase 1.5** — Supabase dual-write from `tracker.py` |
| 2026-05-02 | `d078dbc` | **T2.31 / Phase 2** — Dashboard read-side cutover to Supabase + Realtime push |
| 2026-05-02 | `a2b8410` | **T2.32 / Phase 4** — Railway live game-state worker (10s cadence) |
| 2026-05-02 | `7925fe6` + `8dd0cb7` | **T2.33 / Phase 3** — Railway predictor loop (5min cadence) + start-command fix |
| 2026-05-02 | `8dd0cb7` | **T2.34 / Phase 6** — PWA bundle (manifest + icons + service worker + iOS meta) |
| 2026-05-02 | `91d094c` | **T2.35** — Bypass Next.js fetch cache on dashboard SSR (was serving stale data) |
| 2026-05-02 | `442fe4d` + `6d23152` | **T2.36 + T2.37** — Telegram notifier upgrade (Supabase dedup, HTML format, dashboard hyperlink) + STRONG-only filter |
| 2026-05-02 | `e80a0a1` | **T2.38** — 8 new STRONG-only Telegram event types: graded W/L, voided, pregame, CLV, weather, milestone, daily digest, ops health |
| 2026-05-02 | `e5a0f84` | **T2.39** — Pitcher days-rest feature tested (4 variants on 2024↔2025) and rejected per ship rules; logged in KB.md |
| 2026-05-02 | `2c55af5` | **T2.40 + T2.41** — Pre-game starter-scratch detector (Telegram alert on STRONG bets affected by a probable-pitcher change) + dedup window fix on `flip_to_strong` (5min → 24h, prevents Railway×GHA race-induced duplicate pings) |
| 2026-05-02 | `<this commit>` | **T2.42** — Bankroll equity curve on `/history`: SVG line + drawdown shading + ATH marker + 6-stat panel (Bankroll, ATH, Max DD, Current DD, Vol, Sharpe). +1.4kB bundle. |

End result: the dashboard is now an **installable PWA** receiving **sub-second Realtime push** of model predictions every 5 minutes (Railway predictor) and game state every 10 seconds (Railway live-state worker).

---

## 🔥 Tier 1 — direct ROI moves

These improve money outcomes without touching the working model.

| # | Status | Effort | Edge gain | Item |
|---|---|---|---|---|
| 1 | [needs user opt-in] | 2–3 hr | **+10–30% ROI** | **Kelly fractional bet sizing** in `tracker._apply_odds_to_row`. Currently flat 1u STRONG / 0.5u LEAN. Quarter-Kelly sizing (units = `0.25 × edge / (price-1)`) typically lifts ROI 10-20% without raising variance much. ⚠ **CLAUDE.md "Money rules"** explicitly says "Flat 1u plays only. User explicitly rejected Kelly / fractional / bankroll-aware sizing." Don't ship without re-checking with the user. |
| 2 | [tested 2026-05-02 · rejected] | 3 hr | — | **Pitcher days-rest feature**. `away_days_rest` + `home_days_rest` already backfilled in 2024/2025 backtests; `backfill_days_rest.py` is on disk. Tested 4 variants via `test_days_rest.py` against the Phase E.3 baseline on a 2-split 2024↔2025 cross-validation. Best variant (`+rest_signed_gap`) +8.2u sum P&L vs baseline (below the +10u ship bar) AND regressed STRONG YRFI hit rate by 3.1pp on the 2024→2025 split. Other 3 variants regressed -21 to -60u. Joins the "tested, didn't help" list in `docs/KB.md`. Reason: rest signal isn't separable from the FIP/ERA/last-5 features already in the model. |
| 3 | [previously rejected] | 4–6 hr | — | **Wind-direction × park-orientation**. Per `docs/KB.md` "What's NOT in the model" list, this was previously tested across 2024/2025 backtests and didn't improve out-of-sample Brier. **Don't re-test without a fundamentally new feature engineering approach** (e.g. categorical "blowing OUT to RF / blowing IN from CF / cross-wind" with park-specific orientation, vs the prior continuous interaction). |
| 4 | [shipped 2026-05-02 · T2.40] | 3 hr | Prevents bad-data losses | **Pre-game injury / scratch detection**. Extended the existing Phase-4 live-state worker to also poll `probablePitcher` and compare to our recorded pitcher_id on STRONG bets. Telegram alert via the T2.38 framework, 6h dedup, 60s throttle. See `workers/live_state.py` `check_scratches()`. |
| 5 | [scheduled T+14d] | 6 hr | +2–4% NRFI edge | **Catcher framing** (T4.1 in AUDIT). Remote agent scheduled via `/schedule` to investigate Baseball Savant on 2026-05-16. Backtest gates before any model change. |

---

## 🟡 Tier 2 — visibility & risk control

| # | Status | Effort | Item |
|---|---|---|---|
| 6 | [shipped 2026-05-02 · T2.42] | 3 hr | **Bankroll equity curve** on `/history` page. Pure SVG (no charting lib). Equity line + drawdown shading + ATH watermark + 6-stat panel (Bankroll / ATH / Max DD / Current DD / Vol / Sharpe). +1.4kB bundle. See `EquityCurveChart` in `dashboard/components/HistoryView.tsx`. |
| 7 | [ ] | 2–3 hr | **Live DK line-drift chip** per row. `opened_*_odds` + `clv_pct` already exist; just not surfaced. Shows "DK -135 → -150 (sharp move toward us)". |
| 8 | [ ] | 2 hr | **Drawdown circuit breaker**. Auto-PASS all bets after N consecutive losses or % bankroll drawdown. |
| 9 | [ ] | 3–4 hr | **Ops health card** on dashboard. Last predict cycle, last odds scrape, system_errors today, Railway worker status, parity check. |
| 10 | [ ] | 2 hr | **Today's CLV summary** in summary strip. "Today CLV: +0.8pp avg" — leading indicator of model sharpness. |

---

## 🔵 Tier 3 — model robustness & validation

| # | Status | Effort | Item |
|---|---|---|---|
| 11 | [ ] | 6–8 hr | **Walk-forward backtest framework**. Train on prior season, test on next, retrain monthly. Required before any new feature lands. |
| 12 | [ ] | 3–4 hr | **Confidence intervals on hit rate**. 95% CI Bayesian shading on the +41.99u stat. |
| 13 | [ ] | 5–6 hr | **Model drift detector**. Rolling 30-day calibration test; alarm if P=0.6 doesn't actually win 60%. |
| 14 | [ ] | 6–8 hr | **A/B model harness**. Run two LR variants in parallel, track which would have won. |
| 15 | [ ] | 4 hr | **Backfill tooling**. Generalize the existing `backfill_phase_*.py` scripts into a single `backfill.py --feature X --season Y`. |

---

## 🟢 Tier 4 — UX & convenience

| # | Status | Effort | Item |
|---|---|---|---|
| 16 | [ ] | 10–15 hr | **Auto-bet placement on DK** OR a "bet card" deep link that pre-fills the side + line on DK. |
| 17 | [ ] | 4–6 hr | **iOS native push via Web Push + VAPID**. Service worker stubs already in `dashboard/public/sw.js`. Telegram already covers this; redundant but nicer. |
| 18 | [ ] | 1 hr each | **Slack / Discord webhooks** mirroring the Telegram path. `ALERT_WEBHOOK_URL` already exists. |
| 19 | [ ] | 2 hr | **Slate confidence rating**. "3 STRONG / 5 LEAN / 7 PASS = High confidence" badge in summary strip. |
| 20 | [ ] | 3 hr | **Bet history search + filters** on `/history`. |

---

## ⚪ Tier 5 — speculative / longer-term

| # | Status | Effort | Item |
|---|---|---|---|
| 21 | [ ] | 8–12 hr | **XGBoost replacing LR**. +1-3% accuracy typical. Don't ship without #11 walk-forward. |
| 22 | [ ] | 6–10 hr | **Live in-game hedging logic**. If you bet YRFI -150 and T1 ends 0-0, hedge B1 NRFI for guaranteed mid-bet profit. |
| 23 | [ ] | 16+ hr/sport | **Other sports — NBA Q1, NFL H1, NHL P1**. Same architecture; same data sources. Big TAM, big lift. |
| 24 | [ ] | 12+ hr | **Player props (first-inning pitcher Ks, runs O/U 0.5)**. Different model, same data sources. |

---

## ⛔ Explicitly out of scope

- **Phase 5 multi-book odds** (DK + FanDuel + BetMGM) — user decision: DK only, prioritize speed over best-line shopping.
- **Public betting % integration** — first-inning markets too thin for reliable sharp/square data.
- **DK cash-out automation** — DK's cash-out values are hostile, rarely +EV.

---

## Recommended execution order

If shipping in priority order:

**Week 1**: #1 Kelly sizing → #6 Equity curve → #7 CLV chip
**Week 2**: #4 Injury detector → #8 Drawdown breaker → #9 Ops health card
**Week 3-4**: #11 Walk-forward → #15 Backfill tooling → #2 Days-rest feature
**Month 2+**: #5 Catcher framing (when scheduled agent fires) → #12 CIs → #13 Drift detector → #21 XGBoost A/B

---

## How to update this file

When you ship something:

1. Move its row from the relevant tier into the **Recently shipped** table at the top, with date + commit SHA.
2. Add a corresponding `### Added/Changed/Fixed` entry to `CHANGELOG.md` under today's date section.
3. If the architecture changed (new worker, new table, new data flow), update the diagram in `docs/KB.md`.
4. Commit all three files together so future-you (and future-Claude) see the full picture.

When you discover a new upgrade idea:

1. Drop it in the appropriate tier with `[ ]` status, effort estimate, and edge / value rationale.
2. Tag with audit ID `T<series>.<x>` if it overlaps with an existing audit item.
