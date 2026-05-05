/**
 * GET /api/cron/predict
 *
 * Vercel-cron entry-point that fires GitHub Actions' daily.yml workflow
 * via workflow_dispatch -- a higher-priority trigger than `schedule`,
 * so it runs within seconds instead of GHA's typical 1-3 hour cron lag.
 * Used as an out-of-band redundant trigger so the slate stays fresh
 * even when GHA's free-tier scheduler is backed up.
 *
 * Auth: Vercel Cron sends `Authorization: Bearer <CRON_SECRET>` on
 * every cron request (see https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs).
 * `CRON_SECRET` is REQUIRED -- there is no fallback.  The previous
 * implementation accepted any request with a non-empty
 * `x-vercel-cron-signature` header when CRON_SECRET was unset, but
 * that header is not authenticated and any client can send it, which
 * exposed GitHub workflow_dispatch via this endpoint to arbitrary
 * traffic.  Misconfigured deploys now 500 instead of silently allowing
 * unauthenticated dispatches.
 *
 * Triggers the same `predict` action the dashboard's manual button
 * fires -- runs the predictor, scrapes DK odds, imports them, and
 * commits + pushes the data changes back to the working branch.
 */

import { NextResponse } from "next/server";

export const runtime  = "nodejs";
export const dynamic  = "force-dynamic";

const GITHUB_OWNER  = "joey11600";
const GITHUB_REPO   = "MLB-first-inning";
const WORKFLOW_FILE = "daily.yml";
// Branch configurable via env var (T3.7) so renaming the working
// branch doesn't require a code change.
const TARGET_BRANCH = process.env.TARGET_BRANCH || "claude/mlb-inning-run-predictor-QyazL";

export async function GET(req: Request) {
  // Vercel Cron auth: require Bearer token equal to CRON_SECRET.  No
  // fallback.  If the env var isn't set we fail closed so a
  // misconfigured deploy doesn't accept arbitrary internet traffic.
  const secret = process.env.CRON_SECRET;
  if (!secret) {
    return NextResponse.json(
      {
        error:
          "Server is missing CRON_SECRET env var. Configure it in Vercel " +
          "project settings; this endpoint refuses requests until set.",
      },
      { status: 500 },
    );
  }
  const auth = req.headers.get("authorization") ?? "";
  if (auth !== `Bearer ${secret}`) {
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
      inputs: { action: "predict" },
    }),
  });

  if (ghResp.status === 204) {
    return NextResponse.json({
      ok: true,
      action: "predict",
      dispatchedAt: Date.now(),
      message: "Vercel cron dispatched predict workflow.",
    });
  }

  const errText = await ghResp.text().catch(() => "");
  return NextResponse.json(
    { error: `GitHub API ${ghResp.status}: ${errText.slice(0, 400)}` },
    { status: 502 },
  );
}
