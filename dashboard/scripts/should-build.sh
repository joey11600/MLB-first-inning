#!/usr/bin/env bash
#
# Vercel ignoreCommand.  exit 0 -> SKIP the build.  exit 1 -> BUILD.
#
# WHY THIS EXISTS.  Vercel rebuilds the whole site on every push to the
# deploy branch, and this repo's automation pushes constantly: ~30
# `auto: predict` commits a day, 2 `auto: grade`, 1 `auto: daily backup
# snapshot`.  Measured over 2026-08-01..07, 188 of 242 commits (78%)
# were data-only automation.  Every one triggered a full `next build`,
# and this project alone burned 99h 28m of Build CPU Minutes -- 51.6% of
# the plan for the cycle.  Those builds changed nothing a user can see.
#
# WHY IT IS SAFE HERE, AND THE CHECK THAT PROVED IT.  This is NOT the
# sibling strikeouts project, whose dashboard is a static export that
# fetches live JSON at runtime.  This is a full Next.js app, and
# `dashboard/lib/board.ts:loadBoard()` is Supabase-FIRST with a
# filesystem fallback onto the build-time copy that
# `scripts/copy-data.mjs` bakes in.  So skipping builds is only safe if
# production actually reads Supabase.  Verified empirically on
# 2026-08-07 rather than by reading env vars:
#
#   time      generatedAt                    last deployment
#   14:59:30  2026-08-07T14:58:16.722598Z    14:55:53  9859b163
#   15:01:02  2026-08-07T14:58:16.722598Z    14:55:53  9859b163
#   15:02:33  2026-08-07T15:01:52.965955Z    14:55:53  9859b163   <-- advanced
#
# The served data moved forward with NO new deployment, which a file
# baked into the bundle cannot do.  Corroborating: that timestamp
# carries microseconds and a +00:00 offset (Postgres timestamptz),
# whereas the filesystem branch builds its value from
# `stat.mtime.toISOString()`, which always renders milliseconds and a
# trailing Z.  Different producer.
#
# IF THAT EVER CHANGES -- if Supabase is decommissioned, or
# `isSupabaseConfigured()` starts returning false in production -- this
# script MUST be removed in the same change.  The board would then be
# served from the build bundle, and skipping builds would silently
# freeze the picks on a live money system.  Re-run the check above
# before trusting this file.
#
# KNOWN AND ACCEPTED CONSEQUENCE: the bundled CSVs are now refreshed
# only when a code commit lands, so the Supabase-outage fallback
# degrades from "as fresh as the last build" (minutes) to "possibly days
# stale".  Supabase is the primary and the fallback was always
# best-effort, but that is a real change and it is written down here
# rather than discovered later.
#
# FAIL TOWARD BUILDING, ALWAYS.  A needless build costs a few minutes.
# A wrongly skipped one ships stale code to a production betting
# dashboard and nothing alerts, because from the outside it looks
# exactly like a deploy that worked.

set -u

# --------------------------------------------------------------------
# THE PATHSPEC TRAP -- the reason this script cds before doing anything.
#
# This Vercel project's Root Directory is `dashboard/`, not the repo
# root, so Vercel invokes this command from inside dashboard/.  Git
# pathspecs resolve relative to the CURRENT DIRECTORY, so a bare `data`
# pathspec evaluated from there means `dashboard/data` -- which is
# gitignored, exists only as a build artifact, and therefore appears in
# ZERO commits.
#
# MEASURED, not assumed, because the guess was wrong in direction.  The
# naive pathspec does not skip everything; it skips NOTHING:
#
#   from dashboard/, ':(exclude)data'      auto: predict -> BUILD
#   from dashboard/, ':(exclude,top)data'  auto: predict -> SKIP
#
# because excluding the non-existent dashboard/data leaves the real
# data/ files sitting in the diff, so every commit looks like it has
# non-data changes.  That failure mode is WORSE THAN AN ERROR: the
# deploy succeeds, the dashboard is fine, the classifier reports
# "building" forever, and the only symptom is that the bill never goes
# down.  Nothing alerts on a fix that quietly does nothing.
#
# Two independent guards against it:
#   1. cd to the repo root, so relative paths mean what they look like.
#   2. the `top` pathspec magic below, which forces repo-root-relative
#      resolution even if someone later removes the cd.
# --------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  echo "should-build: not inside a git work tree — BUILDING"
  exit 1
fi
cd "$REPO_ROOT" || { echo "should-build: cannot cd to $REPO_ROOT — BUILDING"; exit 1; }
echo "should-build: invoked from $(dirname "$0"), operating at $REPO_ROOT"

# Everything that is pure data.  `:(exclude,top)data` = "ignore the
# repo-root data/ directory when computing this diff".
DATA_ONLY_PATHS=(
  ':(exclude,top)data'
)

# --------------------------------------------------------------------
# COMPARE AGAINST THE LAST BUILD, NOT THE LAST COMMIT.
#
# Vercel builds ONCE PER PUSH, at the tip -- not once per commit. So
# `HEAD^ HEAD` asks the wrong question. Push a code commit and a data
# commit together and Vercel evaluates only the tip, sees data-only,
# skips, and THE CODE COMMIT NEVER DEPLOYS. That is the failure this
# whole file is written to avoid: a skip that looks successful while
# shipping nothing. The sibling strikeouts project hit it for real and
# fixed it the same way ("compare the build-skip against the last BUILD,
# not the last commit").
#
# VERCEL_GIT_PREVIOUS_SHA is documented as "the git SHA of the last
# successful deployment for the project and branch", and is exposed
# ONLY when an Ignored Build Step is configured -- i.e. exactly here.
# Diffing from it covers every commit since the last real build, however
# many pushes that spans.
#
# THE SHALLOW-CLONE INTERACTION, which gets WORSE as this script gets
# better: Vercel clones shallow, and every skip pushes the last
# SUCCESSFUL build further into the past. At ~30 skipped data commits a
# day the previous build can easily sit outside the fetched history, so
# the sha is set but the object is missing. `git fetch --depth=1` for
# that one commit is enough -- `git diff A B` needs both objects, not
# the path between them.
PREV="${VERCEL_GIT_PREVIOUS_SHA:-}"
BASE=""

if [ -n "$PREV" ]; then
  if ! git cat-file -e "${PREV}^{commit}" 2>/dev/null; then
    echo "should-build: previous build $PREV not in the shallow clone; fetching it"
    git fetch --quiet --depth=1 origin "$PREV" 2>/dev/null || true
  fi
  if git cat-file -e "${PREV}^{commit}" 2>/dev/null; then
    BASE="$PREV"
    echo "should-build: comparing against LAST BUILD ${PREV:0:8}"
  else
    echo "should-build: previous build $PREV still unreachable after fetch"
  fi
else
  echo "should-build: VERCEL_GIT_PREVIOUS_SHA is empty (first run with an ignore step, or no prior success)"
fi

if [ -z "$BASE" ]; then
  # Narrow fallback. Correct for the single-commit push that is the norm
  # here, and strictly better than building unconditionally -- but it
  # cannot see a code commit buried under a data commit in the same
  # push, so say so rather than let it pass silently.
  if ! git rev-parse --verify --quiet HEAD^ >/dev/null 2>&1; then
    echo "should-build: no parent commit either — BUILDING"
    exit 1
  fi
  BASE="$(git rev-parse HEAD^)"
  echo "should-build: NARROW COMPARISON against parent ${BASE:0:8} — a code"
  echo "  commit pushed together with a later data commit would be missed here."
fi

# A redeploy of the same commit diffs to nothing and would skip forever.
# Somebody asked for this deploy explicitly; give it to them.
if [ "$(git rev-parse "$BASE")" = "$(git rev-parse HEAD)" ]; then
  echo "should-build: base == HEAD (redeploy of the same commit) — BUILDING"
  exit 1
fi

# --quiet exits 0 when the filtered diff is EMPTY (i.e. nothing outside
# data/ changed) and 1 when it is not.  It exits 128 on error, which
# falls through to the build branch below -- deliberately.
if git diff --quiet "$BASE" HEAD -- "${DATA_ONLY_PATHS[@]}" 2>/dev/null; then
  echo "should-build: data-only since ${BASE:0:8} — SKIPPING build"
  git log --oneline "$BASE..HEAD" 2>/dev/null | head -10
  exit 0
fi

echo "should-build: non-data changes since ${BASE:0:8} — BUILDING"
git diff --name-only "$BASE" HEAD -- "${DATA_ONLY_PATHS[@]}" 2>/dev/null | head -20
exit 1
