# Manual DK odds overrides

When the auto-scrape misses a DK price for a STRONG bet, the system
silently uses the **−110 fallback** (`+0.909u` for a win, `−1.000u`
for a loss). The 2026-05-09 audit found this had happened to **112
of 220 graded STRONG bets across the season** (51%) -- the dashboard
showed "DK -110\*" with an asterisk to indicate the fallback, but
the row was nominally graded as if -110 were the real price.

This doc covers two new tools that work together to fix that:

1. `data/manual_odds_overrides.csv` -- a ledger you maintain.
2. `tools/apply_manual_odds.py` -- the heal script.
3. The `strong_orphan_no_odds` Telegram ping that fires the moment a
   STRONG bet grades without a captured DK price.

## When to use this

You'll know an override is needed in one of three ways:

- **Telegram pings you with `⚠️ NO DK ODDS CAPTURED`** the moment a
  STRONG bet grades without a real DK price. The body includes the
  exact line you'd add to `manual_odds_overrides.csv` to heal it.
- **Dashboard `OddsChip` shows "DK -110\*"** (with the asterisk) for
  a row that was graded -- you placed the bet on DK at a different
  price than -110.
- **`tools/pl_calc.py --window <range>`** prints the `*` marker in
  the `Odds` column for any fallback row.

## How to add an override

Open `data/manual_odds_overrides.csv` and add a row at the bottom
(format header is in the file already):

```csv
date,away_team,home_team,game_pk,market_nrfi_odds,market_yrfi_odds,sportsbook,note
2026-05-05,MIN,WSH,,+115,-135,DraftKings (manual),"actual DK entry price"
```

Field rules:

- **date** -- ET slate date `YYYY-MM-DD`.
- **away_team / home_team** -- 3-letter abbr (NYY, SF, ...).
- **game_pk** -- optional. Provide it if there's a doubleheader on the
  same date+matchup; otherwise `apply_manual_odds.py` warns and skips.
- **market_nrfi_odds / market_yrfi_odds** -- American odds (`-130`,
  `+105`, `-110`). Enter the side(s) you actually placed; blank
  fields leave the existing column alone.
- **sportsbook** -- defaults to `DraftKings (manual)` if blank, so
  the dashboard chip shows the override label.
- **note** -- free-form, journaled to `pick_changes.csv`.

Commit and push the CSV change. The next predict cron tick (within
~30 min) runs `tools/apply_manual_odds.py`, which:

1. Finds the matching pick row by `(date, game_pk)` or
   `(date, away, home)`.
2. Updates `market_*_odds`, `sportsbook`, `odds_captured_at`.
3. Sets `bet_placed=Y` and `units_risked=1` for STRONG NRFI/YRFI
   rows that weren't already (preserving PASS rows untouched).
4. Recomputes `profit_loss_units` from the supplied odds via
   `tracker._calc_pnl`.
5. Writes a `pick_changes.csv` journal entry per row.
6. Mirrors the changed rows to Supabase.

Idempotent: re-applying the same override is a no-op.

## Manual run

To apply the overrides immediately (e.g. you're testing locally or
want to skip the cron wait):

```bash
# Dry-run -- show what would change:
python tools/apply_manual_odds.py --dry-run

# Apply for real:
python tools/apply_manual_odds.py
```

Both run idempotently against the entire ledger, regardless of
date.

## Doubleheaders

If two games on the same date share the same matchup (rare but
possible), `apply_manual_odds.py` will print a warning and skip
the override unless `game_pk` is supplied. To resolve, look up the
specific game's `game_pk` in `data/picks_2026.csv` and add it to
the override row.

## Related files

- `tools/apply_manual_odds.py` -- the heal script.
- `tools/pl_calc.py` -- canonical P&L calculator; flags fallback
  rows with `*` in the `Odds` column.
- `tracker._calc_pnl` -- payout math; uses real `market_*_odds`
  when populated, falls back to -110 when not.
- `tracker._notify_strong_orphan_no_odds_telegram` -- the
  Telegram alert that fires on grade if odds were never captured.
- `data/pick_changes.csv` -- audit trail of every pick mutation.
