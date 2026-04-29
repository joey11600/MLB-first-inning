/**
 * GET /api/run-job/status?since=DISPATCHED_AT_MS&runId=...
 *
 * Polled by ControlPanel to track a workflow_dispatch run from queue
 * through completion.  Two phases:
 *
 *   1. RUN DISCOVERY (no runId yet)
 *      Caller passes ?since=<dispatchedAt> from the POST response.
 *      We list recent workflow_dispatch runs and pick the one whose
 *      created_at falls within ~120s of "since".  Returns its run_id
 *      so the next poll can be by-id.
 *
 *   2. STATUS POLLING (runId set)
 *      We hit /actions/runs/{id}/jobs and report:
 *        state          = "queued" | "running" | "complete"
 *        currentStep    = name of in-progress step (or last completed step)
 *        conclusion     = "success" | "failure" | "cancelled" | null
 *
 * The ControlPanel uses currentStep to render a friendly progress
 * label like "Predicting today's slate..." and triggers router.refresh()
 * once state==="complete" + conclusion==="success".
 */

import { NextResponse } from "next/server";

export const runtime  = "nodejs";
export const dynamic  = "force-dynamic";

const GITHUB_OWNER = "joey11600";
const GITHUB_REPO  = "MLB-first-inning";
const WORKFLOW_FILE = "daily.yml";

interface GhRun {
  id: number;
  status: string;        // "queued" | "in_progress" | "completed"
  conclusion: string | null;
  created_at: string;
  html_url: string;
  event: string;
  head_branch: string;
}

interface GhStep {
  name: string;
  status: string;
  conclusion: string | null;
  number: number;
}

interface GhJob {
  id: number;
  status: string;
  conclusion: string | null;
  steps: GhStep[];
}


export async function GET(req: Request) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return NextResponse.json({ error: "Missing GITHUB_TOKEN" }, { status: 500 });
  }

  const url = new URL(req.url);
  const sinceParam = url.searchParams.get("since");
  const runIdParam = url.searchParams.get("runId");

  // Phase 2: we already know the run_id, just poll its status.
  if (runIdParam) {
    return await pollRun(runIdParam, token);
  }

  // Phase 1: find the run that matches our dispatch timestamp.
  if (!sinceParam) {
    return NextResponse.json(
      { error: "either ?runId= or ?since= required" },
      { status: 400 },
    );
  }

  const since = Number.parseInt(sinceParam, 10);
  if (!Number.isFinite(since)) {
    return NextResponse.json({ error: "since must be epoch-ms" }, { status: 400 });
  }

  // Pad backward 30s in case the user's clock is slightly ahead of GitHub's
  const sinceIso = new Date(since - 30_000).toISOString();
  const listUrl =
    `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
    `/actions/workflows/${WORKFLOW_FILE}/runs` +
    `?event=workflow_dispatch&per_page=10`;

  const listResp = await fetch(listUrl, {
    headers: ghHeaders(token),
    cache: "no-store",
  });
  if (!listResp.ok) {
    return NextResponse.json(
      { error: `GitHub list runs ${listResp.status}` },
      { status: 502 },
    );
  }
  const listJson = (await listResp.json()) as { workflow_runs?: GhRun[] };
  const runs = listJson.workflow_runs ?? [];

  // Find the run whose created_at >= sinceIso (epoch comparison).
  const sinceTime = Date.parse(sinceIso);
  const match = runs.find((r) => Date.parse(r.created_at) >= sinceTime);
  if (!match) {
    // Run hasn't shown up yet -- usually appears within ~3-5s of dispatch.
    return NextResponse.json({ state: "pending", currentStep: "Queued..." });
  }

  return await pollRun(String(match.id), token, match);
}


async function pollRun(runId: string, token: string, knownRun?: GhRun) {
  // Get the run + its jobs in parallel
  const [runResp, jobsResp] = await Promise.all([
    knownRun
      ? Promise.resolve(null)
      : fetch(
          `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${runId}`,
          { headers: ghHeaders(token), cache: "no-store" },
        ),
    fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${runId}/jobs`,
      { headers: ghHeaders(token), cache: "no-store" },
    ),
  ]);

  let run: GhRun;
  if (knownRun) {
    run = knownRun;
  } else {
    if (!runResp || !runResp.ok) {
      return NextResponse.json({ error: "Failed to fetch run" }, { status: 502 });
    }
    run = (await runResp.json()) as GhRun;
  }

  if (!jobsResp.ok) {
    return NextResponse.json({ error: "Failed to fetch jobs" }, { status: 502 });
  }
  const jobsJson = (await jobsResp.json()) as { jobs?: GhJob[] };
  const jobs = jobsJson.jobs ?? [];

  // Find the in-progress (or most recent) step across all jobs
  let currentStep = "Queued...";
  for (const job of jobs) {
    const inProg = job.steps?.find((s) => s.status === "in_progress");
    if (inProg) {
      currentStep = friendlyStepName(inProg.name);
      break;
    }
    // Otherwise track most recent completed step
    const completed = (job.steps ?? [])
      .filter((s) => s.status === "completed")
      .pop();
    if (completed) {
      currentStep = friendlyStepName(completed.name);
    }
  }

  let state: "pending" | "running" | "complete" = "running";
  if (run.status === "queued") state = "pending";
  if (run.status === "completed") state = "complete";

  return NextResponse.json({
    state,
    currentStep,
    conclusion: run.conclusion,    // null until complete; then success/failure/cancelled
    runId: run.id,
    htmlUrl: run.html_url,
  });
}


/** Map noisy GH step names to short friendly progress labels. */
function friendlyStepName(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("checkout"))      return "Checking out repo...";
  if (n.includes("set up python")) return "Setting up Python...";
  if (n.includes("install"))       return "Installing dependencies...";
  if (n.includes("decide"))        return "Choosing action...";
  if (n.includes("predict"))       return "Generating predictions...";
  if (n.includes("grade"))         return "Grading results...";
  if (n.includes("recalibrate"))   return "Recalibrating model...";
  if (n.includes("scrape") || n.includes("dk_odds")) return "Fetching DK odds...";
  if (n.includes("import-odds"))   return "Computing edges...";
  if (n.includes("commit"))        return "Saving changes...";
  if (n.includes("push"))          return "Deploying to dashboard...";
  if (n.includes("post"))          return "Cleaning up...";
  return name;
}


function ghHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "nrfi-terminal",
  };
}
