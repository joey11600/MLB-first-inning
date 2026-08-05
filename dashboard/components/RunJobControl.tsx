"use client";

/**
 * RunJobControl -- manually dispatch the GitHub Actions predict / grade
 * workflows and watch them run.
 *
 * EXTRACTED from ControlPanel on 2026-08-05. These are OPS controls --
 * they re-run the pipeline, not the view -- and they sat in the middle
 * of the board's filter row, where the operator (who asked for less
 * confusing controls) had to read past "Predict / Grade" buttons every
 * time they changed a date. They now live in the Settings menu, which
 * is where once-in-a-while machinery belongs. Behaviour, endpoints and
 * polling are unchanged; the code moved files, nothing else.
 *
 * Styles stay in ControlPanel.module.css (.runField / .runBtn / ...):
 * the classes were written for this control and nothing else uses them.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./ControlPanel.module.css";

type RunStatus = "idle" | "dispatching" | "running" | "complete" | "error";

interface PollState {
  state: "pending" | "running" | "complete";
  currentStep: string;
  conclusion: string | null;
  runId: number;
  htmlUrl: string;
}

export function RunJobControl() {
  const router = useRouter();
  const [status, setStatus]   = useState<RunStatus>("idle");
  const [message, setMessage] = useState<string>("");
  const [runsUrl, setRunsUrl] = useState<string>("");
  const [progress, setProgress] = useState<string>(""); // friendly step label
  const dispatchedAtRef = useRef<number>(0);
  const runIdRef        = useRef<number | null>(null);
  const pollTimer       = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stop polling on unmount
  useEffect(() => () => {
    if (pollTimer.current) clearInterval(pollTimer.current);
  }, []);

  function stopPolling() {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  async function poll() {
    try {
      const params = runIdRef.current
        ? `runId=${runIdRef.current}`
        : `since=${dispatchedAtRef.current}`;
      const res = await fetch(`/api/run-job/status?${params}`, { cache: "no-store" });
      if (!res.ok) return;   // transient -- keep trying
      const data = (await res.json()) as PollState | { state: "pending"; currentStep: string };

      if ("runId" in data && data.runId) runIdRef.current = data.runId;
      if (data.currentStep) setProgress(data.currentStep);

      if (data.state === "complete") {
        stopPolling();
        const fullData = data as PollState;
        if (fullData.conclusion === "success") {
          setStatus("complete");
          setMessage("Action complete -- waiting for Vercel deploy...");
          // GitHub push -> Vercel deploy takes ~30-60s.  Poll the dashboard
          // data path to detect when fresh CSV is live, then refresh.
          waitForDeployAndRefresh();
        } else {
          setStatus("error");
          setMessage(`Action ${fullData.conclusion ?? "failed"}`);
        }
      }
    } catch {
      /* ignore transient errors; keep polling */
    }
  }

  function waitForDeployAndRefresh() {
    // Vercel rebuilds and deploys ~30-60s after push. Refresh after 45s,
    // and again at 90s if needed (covers slower deploys).
    setProgress("Deploying to dashboard (~45s)...");
    setTimeout(() => {
      setProgress("Loading fresh data...");
      router.refresh();
      // One more refresh in case the first hit cached HTML
      setTimeout(() => {
        router.refresh();
        setProgress("");
        setMessage("Dashboard updated");
      }, 5_000);
    }, 45_000);
  }

  async function trigger(action: "predict" | "grade") {
    stopPolling();
    runIdRef.current = null;
    setStatus("dispatching");
    setMessage("");
    setProgress("");
    try {
      const res = await fetch("/api/run-job", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus("error");
        setMessage(data?.error ?? `HTTP ${res.status}`);
        return;
      }
      dispatchedAtRef.current = Number(data?.dispatchedAt) || Date.now();
      setRunsUrl(data?.runsUrl ?? "");
      setStatus("running");
      setMessage(`${action.toUpperCase()} dispatched`);
      setProgress("Queued...");
      // Start polling every 4s.  GitHub takes 3-8s to surface the run.
      pollTimer.current = setInterval(poll, 4_000);
      // Kick off the first poll immediately so the UI feels responsive
      void poll();
    } catch (e) {
      setStatus("error");
      setMessage(e instanceof Error ? e.message : "Network error");
    }
  }

  const busy = status === "dispatching" || status === "running";

  return (
    <div className={styles.runField}>
      <div className={styles.runRow}>
        <button
          type="button"
          className={styles.runBtn}
          data-tone="nrfi"
          onClick={() => trigger("predict")}
          disabled={busy}
          title="Generate today's slate via GitHub Actions"
        >
          {busy ? "..." : "Predict"}
        </button>
        <button
          type="button"
          className={styles.runBtn}
          data-tone="yrfi"
          onClick={() => trigger("grade")}
          disabled={busy}
          title="Grade today's results via GitHub Actions"
        >
          {busy ? "..." : "Grade"}
        </button>
      </div>

      {(status === "running" || status === "dispatching") && (
        <span
          className={`${styles.runMsg} ${styles.runMsgProgress}`}
          title={`${message}\n${progress}`}
        >
          <span className={styles.runSpinner} aria-hidden />
          {progress || message || "..."}
        </span>
      )}
      {status === "complete" && (
        <span className={`${styles.runMsg} ${styles.runMsgOk}`}>
          {progress || message}
        </span>
      )}
      {status === "error" && (
        <a
          className={`${styles.runMsg} ${styles.runMsgErr}`}
          href={runsUrl || undefined}
          target="_blank"
          rel="noreferrer"
          title={message}
        >
          {message.slice(0, 60)} {runsUrl ? "→ Actions" : ""}
        </a>
      )}
    </div>
  );
}
