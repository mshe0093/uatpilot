"use client";

import { Fragment, useState } from "react";
import { API_URL } from "@/lib/api";
import type { Run, TestCase } from "@/lib/types";
import { StatusBadge, outcomeOf } from "./Status";

export function CaseTable({ cases, runs }: { cases: TestCase[]; runs: Record<string, Run> }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="cases">
      <thead>
        <tr>
          <th style={{ width: 60 }}>ID</th>
          <th style={{ width: 90 }}>Covers</th>
          <th>Test case</th>
          <th style={{ width: 70 }}>Priority</th>
          <th style={{ width: 170 }}>Latest result</th>
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
                <td><StatusBadge outcome={outcome} /></td>
              </tr>
              {isOpen && (
                <tr>
                  <td colSpan={5}>
                    <CaseDetail c={c} run={run} />
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

function CaseDetail({ c, run }: { c: TestCase; run?: Run }) {
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
          <strong>Triage ({run.triage.engine}, {run.triage.confidence} confidence):</strong>{" "}
          {run.triage.explanation}
          {run.triage.floor_applied && (
            <span className="muted"> · Floor applied: the model said “{run.triage.floor_applied.from}”,
              but {run.triage.floor_applied.reasons.join("; ")}.</span>
          )}
        </p>
      )}
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
