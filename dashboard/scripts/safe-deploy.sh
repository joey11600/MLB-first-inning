#!/usr/bin/env bash
# safe-deploy.sh -- guarded wrapper around `vercel --prod` for the dashboard.
#
# Why this exists (T2.19, the 2026-05-01 incident):
#   The Vercel project is auto-wired to deploy on every push to
#   claude/mlb-inning-run-predictor-QyazL.  The GitHub Actions cron
#   pushes ~12 commits/day (each "auto: predict <date>" run).  If a
#   developer or agent runs `vercel --prod` with uncommitted changes,
#   the manual deploy ships local files -- and then the next cron
#   push (within ~60 minutes) triggers a NEW auto-deploy that builds
#   from the REMOTE branch source, *without* the uncommitted changes.
#   That auto-deploy becomes the alias target and silently overwrites
#   the manual deploy.
#
#   This script makes the failure mode impossible by aborting any CLI
#   deploy that has either:
#     - uncommitted local changes, or
#     - a local HEAD that hasn't been pushed to origin
#   ...so the only deploy state the CLI ships is one that the next
#   cron push won't be able to "win" against.
#
# Usage:
#   cd dashboard && npm run deploy
#
# Exits non-zero (and refuses to deploy) on:
#   - Working tree dirty (any unstaged or untracked changes -- excluding
#     a small whitelist of build artifacts)
#   - Local branch ahead/behind origin
#   - Vercel CLI failure
#
# The CANONICAL deploy procedure is still `git push` -- Vercel auto-
# deploys from the push.  This script is for cases where you want to
# deploy from local without first triggering a fresh build (e.g. a
# rebuild with the same source after a preview test).

set -euo pipefail

cd "$(dirname "$0")/.."   # operate from dashboard/

REPO_ROOT="$(git rev-parse --show-toplevel)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
EXPECTED_BRANCH="claude/mlb-inning-run-predictor-QyazL"

echo "==> safe-deploy: branch=$BRANCH"

if [[ "$BRANCH" != "$EXPECTED_BRANCH" ]]; then
  echo ""
  echo "  REFUSING TO DEPLOY: current branch is '$BRANCH', expected"
  echo "  '$EXPECTED_BRANCH'.  Production deploys come from that"
  echo "  branch only.  Switch branches or push your work to the"
  echo "  expected branch first."
  exit 2
fi

# ---------------------------------------------------------------------
# Working-tree clean check
# ---------------------------------------------------------------------
# `git status --porcelain` outputs one line per modified/untracked file
# (empty output = clean).  We allow an explicit whitelist of files that
# are build artifacts or local-only:
#   - dashboard/tsconfig.tsbuildinfo  (typescript build cache)
#   - dashboard/.next/                (next.js build output)
#   - dashboard/data/                 (prebuild copy of ../data)
#   - .vercel/                        (vercel CLI cache)
WHITELIST_PATTERN='^(\?\?|.M|M.|.A|A.) (dashboard/tsconfig\.tsbuildinfo|dashboard/\.next/|dashboard/data/|\.vercel/)'

DIRTY="$(cd "$REPO_ROOT" && git status --porcelain | grep -vE "$WHITELIST_PATTERN" || true)"

if [[ -n "$DIRTY" ]]; then
  echo ""
  echo "  REFUSING TO DEPLOY: working tree has uncommitted changes."
  echo ""
  echo "$DIRTY" | head -20 | sed 's/^/    /'
  echo ""
  echo "  Why: a CLI deploy ships your local files, but the next cron"
  echo "  push (within ~60 minutes) will trigger an auto-deploy from"
  echo "  the remote branch source -- which doesn't have your uncommitted"
  echo "  changes -- and that auto-deploy will overwrite this one."
  echo ""
  echo "  Fix: commit your changes and push.  Vercel auto-deploys from"
  echo "  the push -- the CLI deploy is unnecessary in that flow."
  echo ""
  echo "    git add <files>"
  echo "    git commit -m \"...\""
  echo "    git push origin $EXPECTED_BRANCH"
  echo ""
  exit 3
fi

# ---------------------------------------------------------------------
# Local-vs-remote sync check
# ---------------------------------------------------------------------
echo "==> safe-deploy: fetching origin to compare local vs remote..."
(cd "$REPO_ROOT" && git fetch --quiet origin "$EXPECTED_BRANCH")

LOCAL_HEAD="$(cd "$REPO_ROOT" && git rev-parse HEAD)"
REMOTE_HEAD="$(cd "$REPO_ROOT" && git rev-parse "origin/$EXPECTED_BRANCH")"

if [[ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]]; then
  echo ""
  echo "  REFUSING TO DEPLOY: local HEAD differs from origin/$EXPECTED_BRANCH."
  echo ""
  echo "  local:  $LOCAL_HEAD"
  echo "  remote: $REMOTE_HEAD"
  echo ""
  AHEAD="$(cd "$REPO_ROOT" && git rev-list --count "origin/$EXPECTED_BRANCH..HEAD")"
  BEHIND="$(cd "$REPO_ROOT" && git rev-list --count "HEAD..origin/$EXPECTED_BRANCH")"
  echo "  local is ahead by $AHEAD commit(s) and behind by $BEHIND commit(s)."
  echo ""
  if [[ "$AHEAD" -gt 0 ]]; then
    echo "  Action: push your local commits first."
    echo "    git push origin $EXPECTED_BRANCH"
  fi
  if [[ "$BEHIND" -gt 0 ]]; then
    echo "  Action: rebase on origin first (don't merge -- linear history)."
    echo "    git pull --rebase origin $EXPECTED_BRANCH"
  fi
  echo ""
  echo "  Then re-run: npm run deploy"
  exit 4
fi

# ---------------------------------------------------------------------
# Sanity-check passed -- proceed with the deploy
# ---------------------------------------------------------------------
echo "==> safe-deploy: working tree clean, local HEAD == origin/$EXPECTED_BRANCH ($LOCAL_HEAD)"
echo "==> safe-deploy: invoking 'vercel --prod --yes --archive=tgz' from repo root..."

cd "$REPO_ROOT"
exec npx vercel --prod --yes --archive=tgz
