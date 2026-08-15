# The AI-written X post

Every night, `tools/cards/make_post.py` writes the short post that goes with
the cards, and `/cards` shows it above that night's images with a **Copy**
button. It runs on the hourly predict cron, right after the card is drawn.

Without an API key it still produces a post — a plain, deterministic
template. The key only changes how good the writing is, never whether the
job runs.

---

## Turning the AI part on

You need an OpenRouter account. OpenRouter is a single service that gives
access to lots of different AI models through one key, so we are not tied to
one provider. **I can't create the account or the key for you** — that
involves entering payment and credentials — so here is exactly what to click.

### 1. Make the account and the key

1. Go to **https://openrouter.ai** and click **Sign in** (top right). You can
   sign in with Google or GitHub.
2. Click your avatar (top right) → **Credits** → **Add credits**. Ten dollars
   is a lot here: each post costs a fraction of a cent, and the job runs about
   a dozen times a day.
3. Click your avatar again → **Keys** → **Create key**.
4. Give it a name like `backfist-cards`, leave the credit limit blank, click
   **Create**.
5. **Copy the key immediately** — it starts with `sk-or-v1-` and OpenRouter
   will not show it again. Paste it somewhere safe for the next step.

### 2. Give the key to the nightly job

This is a GitHub "secret" — an encrypted value the automated job can read but
nobody can see afterwards.

1. Go to **https://github.com/joey11600/MLB-first-inning**
2. Click **Settings** (top row of the repo, far right).
3. In the left sidebar: **Secrets and variables** → **Actions**.
4. Click the green **New repository secret** button.
5. **Name:** `OPENROUTER_API_KEY`
6. **Secret:** paste the `sk-or-v1-...` key.
7. Click **Add secret**.

That's it. The next hourly run picks it up. Nothing needs redeploying.

### 3. (Optional) Run it on your own machine too

Open `.env` in the project folder and add one line:

```
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

Then you can preview any night's post without waiting for the cron:

```bash
python tools/cards/make_post.py --date 2026-08-15
```

### 4. (Optional) Change which AI writes it

Default is Claude Sonnet. To use a different one, add a repository
**variable** (same page as step 2, but the **Variables** tab) named
`OPENROUTER_MODEL`, set to any slug from
https://openrouter.ai/models — for example `anthropic/claude-opus-4.1` or
`google/gemini-2.5-pro`. If the slug is wrong or retired, the script falls
back through a short list rather than dropping the post.

---

## What stops it making things up

This is the part that matters, because the post is public and it is selling
something. Three separate controls, not one.

**1. The header is not written by the AI.** The first two lines — the play,
the units, the side, the price — are built in Python straight from the ledger
row, the same way the card builds them. The model cannot reach them. This is
the T8.30 rule: the money-facing line keys off what the system actually
staked.

**2. The AI is handed finished sentences, not numbers to work with.** It
receives facts like *"Andrew Abbott has kept the first inning scoreless in 4
of his last 5 starts"* — already phrased, already correct. It never has to
divide, average or compare anything, so it cannot get the arithmetic wrong.

**3. Every number it writes is checked before publication.**
`_unsourced_numbers` pulls each figure out of the paragraph and confirms it
came from the facts it was given. If it invents one, the script asks again
with the offending number quoted back; if it does it twice, the post falls
back to the plain template and no AI text is published at all.

The check is deliberately asymmetric:

| written | rule | why |
|---|---|---|
| `3.7` for a supplied `3.67` | **allowed** | a rounding, not an invention — rejecting it would push us to the dull template for nothing |
| `6.12` for a supplied `4.15` | **rejected** | a different value |
| `8` in "8 of their last 10" | **rejected** | integers must match exactly — under rounding rules this would sneak through as a rounded `7.58` strikeouts-per-nine |

The seed allowlist is a single entry (`1`, for "the 1st"). It started as
`{1, 3, 5, 9}` and seeding `3` alone was enough to let *"homered in 3
straight games"* — a complete fabrication — pass. Every seeded integer is a
free pass handed to a made-up count.

### The honest limit

The guard catches invented **numbers**. It cannot catch an invented claim
with no number in it — *"he's been shaky early all month"*. The prompt
forbids that, and the fact list is rich enough that the model has no reason
to reach for it, but a prompt is a request rather than a guarantee. **Read
the post before you post it.** It is one short paragraph and it is sitting
right there on `/cards` next to the Copy button.

---

## Files

| file | what it does |
|---|---|
| `tools/cards/make_post.py` | builds the facts, calls OpenRouter, checks the output, publishes |
| `tests/test_post_fabrication_guard.py` | pins the guard's behaviour, including the two bugs above |
| `dashboard/components/CardsView.tsx` | the post block and its Copy button |
| `.github/workflows/daily.yml` | the `Publish + prune Backfist cards` step |
