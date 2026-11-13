"use client";

import type { Run, TestCase, TraceRow } from "@/lib/types";
import { ChangeTag, StatusBadge, outcomeOf } from "./Status";

/** Every case with its latest result. A row opens the case sheet (click or Enter). */
export function CaseTable({ cases, runs, onOpen }: {
  cases: TestCase[];
  runs: Record<string, Run>;
  onOpen: (caseId: string) => void;
}) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th style={{ width: 70 }}>ID</th>
          <th>Test case</th>
          <th style={{ width: 110 }}>Covers</th>
          <th style={{ width: 80 }}>Priority</th>
          <th style={{ width: 220 }}>Latest result</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((c) => {
          const run = runs[c.id];
          const outcome = run
            ? outcomeOf(run.status, run.triage?.label)
            : outcomeOf(c.latest_run?.status, c.latest_run?.triage_label);
          return (
            <tr key={c.id} className="clickable" tabIndex={0} onClick={() => onOpen(c.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(c.id); }
              }}>
              <td className="mono muted">{c.id.split("-").pop()}</td>
              <td>{c.title}</td>
              <td className="mono">{c.requirement_ref}</td>
              <td className="muted">{c.priority}</td>
              <td><span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                <StatusBadge outcome={outcome} /><ChangeTag change={run?.change} />
              </span></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** One row per acceptance criterion in the requirements document. */
export function TraceTable({ rows, onOpenCase, caseIdFor }: {
  rows: TraceRow[];
  onOpenCase: (caseId: string) => void;
  caseIdFor: (shortId: string) => string | undefined;
}) {
  const verdict = (r: string) =>
    r === "PASS" ? <StatusBadge outcome="pass" /> :
    r.startsWith("FAIL") ? <StatusBadge outcome="real_regression" /> :
    r === "MISSING" ? <span className="badge real_regression">No test case</span> :
    r.startsWith("NOT COVERED") ? <span className="muted">Not testable: {r.replace("NOT COVERED: ", "")}</span> :
    <StatusBadge outcome="not_run" />;
  return (
    <table className="data">
      <thead>
        <tr><th style={{ width: 120 }}>Criterion</th><th>Test cases</th><th style={{ width: 260 }}>Result</th></tr>
      </thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.criterion}>
            <td className="mono">{t.criterion}</td>
            <td>
              {t.cases.length === 0 ? <span className="muted">None</span> : t.cases.map((id) => {
                const full = caseIdFor(id);
                return full
                  ? <button key={id} className="btn ghost mono" style={{ height: 26, padding: "0 6px" }} onClick={() => onOpenCase(full)}>{id}</button>
                  : <span key={id} className="mono">{id}</span>;
              })}
            </td>
            <td>{verdict(t.result)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
