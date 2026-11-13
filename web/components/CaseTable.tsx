"use client";

import { Fragment, useState } from "react";
import { API_URL, putJSON } from "@/lib/api";
import type { Run, TestCase, Triage, TriageLabel } from "@/lib/types";
import { ChangeTag, StatusBadge, outcomeOf } from "./Status";

const ENGINE: Record<Triage["engine"], string> = {
  claude: "Claude",
  rules: "rules",
  reused: "reused from an identical earlier failure",
  human: "reviewer",
};

export function CaseTable({ cases, runs, token, canWrite, onReviewed }: {
  cases: TestCase[];
  runs: Record<string, Run>;
  token: string;
  canWrite: boolean;
  onReviewed: (run: Run) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="cases">
      <thead>
        <tr>
          <th style={{ width: 60 }}>ID</th>
          <th style={{ width: 90 }}>Covers</th>
          <th>Test case</th>
          <th style={{ width: 70 }}>Priority</th>
          <th style={{ width: 200 }}>Latest result</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((c) => {
          const run = runs[c.id];
          const outcome = run
            ? outcomeOf(run.status, run.triage?.label)
            : outcomeOf(c.latest_run?.status, c.latest_run?.triage_label);
          const isOpen = open === c.id;
          return (
            <Fragment key={c.id}>
              <tr className="row" onClick={() => setOpen(isOpen ? null : c.id)} aria-expanded={isOpen}>
                <td className="mono">{c.id.split("-").pop()}</td>
                <td className="small">{c.requirement_ref}</td>
                <td>{c.title}</td>
                <td className="small">{c.priority}</td>
                <td><StatusBadge outcome={outcome} /><ChangeTag change={run?.change} /></td>
              </tr>
              {isOpen && (
                <tr>
                  <td colSpan={5}>
                    <CaseDetail c={c} run={run} token={token} canWrite={canWrite} onReviewed={onReviewed} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function CaseDetail({ c, run, token, canWrite, onReviewed }: {
  c: TestCase;
  run?: Run;
  token: string;
  canWrite: boolean;
  onReviewed: (run: Run) => void;
}) {
  const shot = run?.evidence?.screenshot as string | undefined;
  return (
    <div className="detail">
      {c.preconditions.length > 0 && (
        <>
          <strong className="small">Preconditions</strong>
          <ul className="small">{c.preconditions.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </>
      )}
      <strong className="small">Steps</strong>
      <ol className="small">{c.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      <div className="small"><strong>Expected:</strong> {c.expected_result}</div>
      {run?.triage && (
        <p className="small">
          <strong>Triage ({ENGINE[run.triage.engine]}, {run.triage.confidence} confidence):</strong>{" "}
          {run.triage.explanation}
          {run.triage.floor_applied && (
            <span className="muted"> · Floor applied: “{run.triage.floor_applied.from}” was claimed,
              but {run.triage.floor_applied.reasons.join("; ")}.</span>
          )}
        </p>
      )}
      {run?.status === "fail" && canWrite && <Review run={run} token={token} onReviewed={onReviewed} />}
      {shot && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`${API_URL}/api/artifacts/${shot}`} alt="Screenshot at the failing step"
          style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
      )}
      <details>
        <summary className="small">Executable check ({c.executable.kind.toUpperCase()})</summary>
        <pre className="evidence mono">{JSON.stringify(c.executable, null, 2)}</pre>
      </details>
      {run && (
        <details open={run.status === "fail"}>
          <summary className="small">Evidence from the latest run ({run.duration_ms} ms)</summary>
          <pre className="evidence mono">{JSON.stringify(run.evidence, null, 2)}</pre>
        </details>
      )}
      {!run && <p className="muted small">Run the suite (or open a past run) to see evidence.</p>}
    </div>
  );
}

const LABELS: { value: TriageLabel; text: string }[] = [
  { value: "real_regression", text: "Regression" },
  { value: "flaky", text: "Flaky" },
  { value: "environment_issue", text: "Environment" },
  { value: "test_defect", text: "Test defect" },
];

/** A reviewer's decision. It relabels the failure (never the pass/fail status) and is reused
 *  for later identical failures of this case before the model is asked. */
function Review({ run, token, onReviewed }: { run: Run; token: string; onReviewed: (run: Run) => void }) {
  const [label, setLabel] = useState<TriageLabel>(run.triage?.label ?? "real_regression");
  const [text, setText] = useState(run.triage?.engine === "human" ? run.triage.explanation : "");
  const [state, setState] = useState<"idle" | "saving" | "saved" | string>("idle");

  async function save() {
    setState("saving");
    try {
      const triage = await putJSON<Triage>(`/api/runs/${run.id}/triage`, { label, explanation: text }, token);
      onReviewed({ ...run, triage });
      setState("saved");
    } catch (e) {
      setState((e as Error).message);
    }
  }

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <strong className="small">Reviewer decision</strong>
      <div className="review">
        <select value={label} onChange={(e) => setLabel(e.target.value as TriageLabel)} aria-label="Triage label">
          {LABELS.map((l) => <option key={l.value} value={l.value}>{l.text}</option>)}
        </select>
        <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Reason"
          placeholder="Why (shown in the sign-off document)" />
        <button className="btn" disabled={text.trim().length < 10 || state === "saving"} onClick={save}>
          {state === "saving" ? "Saving…" : "Save"}
        </button>
      </div>
      {state === "saved" && <p className="small muted">Saved. Identical failures of this case will reuse it.</p>}
      {!["idle", "saving", "saved"].includes(state) && <p className="small error">{state}</p>}
    </div>
  );
}
