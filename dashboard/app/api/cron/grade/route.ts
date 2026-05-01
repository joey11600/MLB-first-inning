/**
 * GET /api/cron/grade
 *
 * Vercel-cron entry-point that fires the daily.yml `grade` action via
 * workflow_dispatch.  Used as the reliable nightly grader so we don't
 * depend on GitHub Actions' free-tier `schedule` (observed firing
 * 2-3 hours late on average -- e.g. "30 3 * * *" / 11:30 PM ET grade
 * actually fired at 06:25 UTC = 2:25 AM ET).
 *
 * Auth: same pattern as /api/cron/predict -- prefer
 * `Authorization: Bearer <CRON_SECRET>`, fall back to the
 * `x-vercel-cron-signature` header that Vercel sets on internal
 * cron invocations.
 */

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const GITHUB_OWNER  = "joey11600";
const GITHUB_REPO   = "MLB-first-inning";
const WORKFLOW_FILE = "daily.yml";
const TARGET_BRANCH = "claude/mlb-inning-run-predictor-QyazL";

export async function GET(req: Request) {
  const auth   = req.headers.get("authorization") ?? "";
  const sig    = req.headers.get("x-vercel-cron-signature") ?? "";
  const secret = process.env.CRON_SECRET;
  const ok =
    (secret && auth === `Bearer ${secret}`) ||
    (!secret && sig.length > 0);
  if (!ok) {
    return NextResponse.json(
      { error: "Unauthorized -- this endpoint is for Vercel cron only." },
      { status: 401 },
    );
  }

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return NextResponse.json(
      {
        error:
          "Server is missing GITHUB_TOKEN env var. Set a fine-grained PAT " +
          "(Actions: read+write on this repo) in Vercel project settings.",
      },
      { status: 500 },
    );
  }

  const url =
    `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
    `/actions/workflows/${WORKFLOW_FILE}/dispatches`;

  const ghResp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "nrfi-terminal-cron",
    },
    body: JSON.stringify({
      ref: TARGET_BRANCH,
      inputs: { action: "grade" },
    }),
  });

  if (ghResp.status === 204) {
    return NextResponse.json({
      ok: true,
      action: "grade",
      dispatchedAt: Date.now(),
      message: "Vercel cron dispatched grade workflow.",
    });
  }

  const errText = await ghResp.text().catch(() => "");
  return NextResponse.json(
    { error: `GitHub API ${ghResp.status}: ${errText.slice(0, 400)}` },
    { status: 502 },
  );
}
