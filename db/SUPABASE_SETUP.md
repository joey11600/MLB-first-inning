# Supabase Setup — Phase 1 of the Real-Time Architecture

This guide walks through creating the Supabase project, applying the schema,
and migrating the existing CSV data.  After this is complete, the predictor
can dual-write to both CSV and Supabase, and the dashboard can be flipped
to read from Supabase with realtime subscriptions in a separate phase.

**Estimated time**: 30-45 minutes.

## Prerequisites

- A free Supabase account at https://supabase.com (no credit card required)
- Python 3.13 (already installed)
- The repo at its current state (T2.28+)

## Step 1 — Create the Supabase project

1. Go to https://supabase.com and click **New Project**.
2. Choose:
   - **Name**: `nrfi-terminal`
   - **Database password**: generate a strong one and **save it in your password manager**. You probably won't need it for the dashboard (we use the API key) but you'll need it if you ever connect a SQL client directly.
   - **Region**: pick the one closest to where the Railway worker will run (us-east is fine for everything if you're on the East Coast)
   - **Pricing plan**: Free.  Upgrade to Pro ($25/mo) only if you outgrow the free tier limits (50K rows, 500MB DB, 2GB egress) — easily 1+ year of comfort for our data scale.
3. Wait ~2 min for the project to provision.

## Step 2 — Apply the schema

1. In the Supabase dashboard, open **SQL Editor** (left sidebar, looks like `</>`).
2. Click **New query**.
3. Copy the **entire contents of `db/schema.sql`** and paste into the query editor.
4. Click **Run** (Ctrl+Enter).  Should complete in ~5 seconds.
5. Verify: open **Table Editor** in the sidebar — you should see five tables:
   - `picks_2026`
   - `pick_changes`
   - `system_errors`
   - `live_game_state`
   - `odds_history`

If anything errored, copy the error message and let me know — usually it's a typo
in a column type or a missing extension.

## Step 3 — Get the connection credentials

Two pieces, both found in **Settings → API** (left sidebar gear icon):

- **Project URL**: e.g., `https://xyzabc.supabase.co`
- **service_role key**: under "Project API keys", the second one (NOT `anon`).
  This bypasses Row Level Security — required for our migration script
  and for the predictor's writes.

⚠️ **The service_role key is a secret.**  Never:
- Check it into git
- Expose it to client-side JavaScript
- Share screenshots showing it

We'll only use it in:
- The local migration script (env vars)
- The Railway worker (env vars in their dashboard)
- GitHub Actions secrets (for the dual-write rollout)

The dashboard uses a different `anon` key with read-only RLS policies.

## Step 4 — Set local env vars

Create a `.env` file at the repo root (gitignored — verify with `git check-ignore .env`):

```bash
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_KEY=<service-role key from step 3>
```

The migration script auto-loads `.env` via `python-dotenv`.

## Step 5 — Install Python dependencies

```bash
pip install supabase python-dotenv
```

These are pinned in the migration script's docstring so no `requirements.txt`
update is needed yet — we'll add them once the dual-write goes live.

## Step 6 — Dry-run the migration

```bash
python db/migrate_csv_to_supabase.py --dry-run
```

This parses the CSVs, transforms each row, and reports counts WITHOUT writing
to Supabase.  Verify the read count matches what's actually in your CSV
(`wc -l data/picks_2026.csv` minus 1 for the header).

## Step 7 — Run the real migration

```bash
python db/migrate_csv_to_supabase.py
```

Output should look like:
```
Connecting to https://<...>.supabase.co...
  connected.

Migrating data from .../data...

--- picks_2026.csv -> picks_2026 ---
  [picks_2026] read 413 rows from CSV
  [picks_2026] upserted 413/413

--- pick_changes.csv -> pick_changes ---
  [pick_changes] read 23 rows from CSV
  [pick_changes] inserted 23/23

--- system_errors.csv -> system_errors ---
  [system_errors] read 0 rows from CSV

Done.  picks_2026: 413 read, 413 upserted
```

## Step 8 — Verify in Supabase

1. Open **Table Editor → picks_2026** in the Supabase dashboard.
2. Filter by `date = 2026-04-30` to spot-check recent rows.
3. Confirm a row's pick_side, market_yrfi_odds, profit_loss_units etc. match the CSV.
4. Spot-check a JSONB column: click on `home_lineup_json` for any row — should
   show the parsed lineup array, not the literal string `"[]"`.

## Step 9 — What happens next (Phase 1 → Phase 2)

After Step 8 passes, the next session work is:

- **Phase 1.5** (dual-write): modify `tracker.log_picks` and `tracker.grade_date`
  to write to BOTH the CSV (existing flow) AND Supabase (new flow).  Run for
  a week with both writes active.  Compare counts daily — if they ever
  disagree, investigate.

- **Phase 2** (read-side cutover): modify `dashboard/lib/board.ts` to
  subscribe to Supabase realtime instead of reading CSVs at request time.
  At this point Vercel rebuilds become unnecessary — the dashboard pushes
  updates within ~200ms of any DB row change.

- **Phase 3** (Railway predictor): move the predictor cron from GHA to a
  Railway worker.  Run every 5 min instead of hourly.  GHA stays as a
  daily backup catch-up.

Follow `db/migrate_csv_to_supabase.py` for the data shape; the dual-write
flow in tracker.py will use the same transforms.
