/**
 * POST /api/run-job
 *
 * Triggers the daily.yml workflow on GitHub via workflow_dispatch.
 * Body: { action: "predict" | "grade", secret?: string }
 *
 * Auth (T3.3):
 *   - GITHUB_TOKEN env var must be set server-side (PAT for the dispatch).
 *   - If RUN_JOB_SECRET env var is set, the request must include a
 *     matching `secret` field in the JSON body.  The dashboard's manual
 *     button reads window.localStorage.runJobSecret and forwards it.
 *     Without RUN_JOB_SECRET set the endpoint stays open (back-compat
 *     for users who haven't enabled the gate yet).
 *
 * Set both secrets in Vercel project settings -> Environment Variables.
 */

import { NextResponse } from "next/server";

export const runtime  = "nodejs";
export const dynamic  = "force-dynamic";

const GITHUB_OWNER  = "joey11600";
const GITHUB_REPO   = "MLB-first-inning";
const WORKFLOW_FILE = "daily.yml";
// Branch is configurable via env var so renaming the working branch
// doesn't require a code change (T3.7).
const TARGET_BRANCH = process.env.TARGET_BRANCH || "claude/mlb-inning-run-predictor-QyazL";

export async function POST(req: Request) {
  let body: { action?: string; secret?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  // Optional auth gate -- only enforced if the env var is set.
  const expectedSecret = process.env.RUN_JOB_SECRET;
  if (expectedSecret) {
    const provided = (body.secret || "").trim();
    if (!provided || provided !== expectedSecret) {
      return NextResponse.json(
        { error: "Unauthorized: missing or invalid run-job secret." },
        { status: 401 },
      );
    }
  }

  const action = body.action;
  if (action !== "predict" && action !== "grade") {
    return NextResponse.json(
      { error: "action must be 'predict' or 'grade'" },
      { status: 400 },
    );
  }

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return NextResponse.json(
      {
        error:
          "Server is missing GITHUB_TOKEN env var. " +
          "Set a fine-grained PAT (Actions: read+write on this repo) in Vercel.",
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
      "User-Agent": "nrfi-terminal",
    },
    body: JSON.stringify({
      ref: TARGET_BRANCH,
      inputs: { action },
    }),
  });

  if (ghResp.status === 204) {
    // Capture the dispatch moment in epoch-ms so the status endpoint can
    // find OUR run (filter recent workflow_dispatch runs to created >= this).
    return NextResponse.json({
      ok: true,
      action,
      dispatchedAt: Date.now(),
      message: `Workflow dispatched. Polling for progress...`,
      runsUrl: actionsUrl(),
    });
  }

  const errText = await ghResp.text().catch(() => "");
  return NextResponse.json(
    {
      error: `GitHub API ${ghResp.status}: ${errText.slice(0, 400)}`,
    },
    { status: 502 },
  );
}

function actionsUrl(): string {
  return `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}`;
}
