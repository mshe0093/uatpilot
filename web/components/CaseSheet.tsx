"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { API_URL, getJSON, putJSON } from "@/lib/api";
import type { Matrix, Run, TestCase, Triage, TriageLabel } from "@/lib/types";
import { ChangeTag, OUTCOMES, StatusBadge, outcomeOf } from "./Status";

function labelledBy(t: Triage): string {
  switch (t.engine) {
    case "llm": return `Labelled by ${t.model ? t.model.split(":").slice(1).join(":") : "the model"}`;
    case "claude": return "Labelled by Claude";
    case "rules": return "Labelled by the rules engine";
    case "reused": return "Copied from an identical earlier failure";
    case "human": return "Labelled by a reviewer";
  }
}

/** One test case: what it checks, its history, the selected run's triage and evidence. A side
 *  sheet rather than an inline expansion, so the matrix and table never jump. Esc closes it. */
export function CaseSheet({ testCase, runId, latestRun, matrix, canWrite, token, onClose, onReviewed }: {
  testCase: TestCase | null;
  runId: string | null; // a specific run picked in the matrix; otherwise the latest run
  latestRun?: Run;
  matrix: Matrix | null;
  canWrite: boolean;
  token: string;
  onClose: () => void;
  onReviewed: (run: Run) => void;
}) {
  const [picked, setPicked] = useState<Run | null>(null);

  useEffect(() => {
    setPicked(null);
    if (runId && runId !== latestRun?.id) {
      getJSON<Run>(`/api/runs/${runId}`).then(setPicked).catch(() => setPicked(null));
    }
  }, [runId, latestRun?.id]);

  const run = picked ?? latestRun;
  const row = matrix?.rows.find((r) => r.case_id === testCase?.id);
  const shot = run?.evidence?.screenshot as string | undefined;

  return (
    <Dialog.Root open={testCase !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="sheet" aria-describedby={undefined}>
          {testCase && (
            <>
              <div className="sheet-head">
                <div className="top">
                  <span className="mono muted">{testCase.id.split("-").pop()} covers {testCase.requirement_ref}</span>
                  <span style={{ flex: 1 }} />
                  <Dialog.Close className="btn ghost" aria-label="Close"><X size={18} /></Dialog.Close>
                </div>
                <Dialog.Title asChild><h2>{testCase.title}</h2></Dialog.Title>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <StatusBadge outcome={run ? outcomeOf(run.status, run.triage?.label) : "not_run"} />
                  <ChangeTag change={run?.change} />
                  <span className="muted small">Priority {testCase.priority}</span>
                  {run && <span className="muted small">{picked ? `Run from ${new Date(run.run_at).toLocaleString()}` : "Latest run"}</span>}
                </div>
              </div>

              <div className="sheet-body">
                {row && row.cells.some(Boolean) && (
                  <section>
                    <h4>History</h4>
                    <div className="history" role="img" aria-label="Result of this case in each run, oldest first">
                      {row.cells.map((c, i) => (
                        <i key={i} title={`Run ${i + 1}`}
                          style={{ background: c ? OUTCOMES[outcomeOf(c.status, c.label)].color : undefined,
                                   outline: c?.run_id === run?.id ? "2px solid var(--accent)" : undefined, outlineOffset: 1 }} />
                      ))}
                    </div>
                  </section>
                )}

                {run?.triage && (
                  <section className="triage-box">
                    <h4 style={{ margin: 0 }}>Why it failed</h4>
                    <div>{run.triage.explanation}</div>
                    <div className="muted small">
                      {labelledBy(run.triage)}, {run.triage.confidence} confidence.
                      {run.triage.floor_applied && (
                        <> The evidence did not support “{run.triage.floor_applied.from.replace("_", " ")}”: {run.triage.floor_applied.reasons.join("; ")}.</>
                      )}
                    </div>
                    {run.status === "fail" && canWrite && <Review run={run} token={token} onReviewed={(r) => { setPicked(picked ? r : null); onReviewed(r); }} />}
                  </section>
                )}

                <section>
                  <h4>Steps</h4>
                  <ol>{testCase.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
                </section>
                {testCase.preconditions.length > 0 && (
                  <section>
                    <h4>Before you start</h4>
                    <ul>{testCase.preconditions.map((p, i) => <li key={i}>{p}</li>)}</ul>
                  </section>
                )}
                <section>
                  <h4>Expected result</h4>
                  <div>{testCase.expected_result}</div>
                </section>

                {shot && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={`${API_URL}/api/artifacts/${shot}`} alt="Screenshot of the page at the failing step"
                    style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--hairline)" }} />
                )}
                {run && (
                  <details open={run.status === "fail"}>
                    <summary>Evidence ({run.duration_ms} ms)</summary>
                    <pre className="evidence">{JSON.stringify(run.evidence, null, 2)}</pre>
                  </details>
                )}
                <details>
                  <summary>Executable check ({testCase.executable.kind === "api" ? "HTTP" : "browser"})</summary>
                  <pre className="evidence">{JSON.stringify(testCase.executable, null, 2)}</pre>
                </details>
                {!run && <p className="muted">This case hasn’t run yet. Run the suite to see its result and evidence here.</p>}
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const LABELS: { value: TriageLabel; text: string }[] = [
  { value: "real_regression", text: "Regression" },
  { value: "flaky", text: "Flaky" },
  { value: "environment_issue", text: "Environment" },
  { value: "test_defect", text: "Test defect" },
];

/** A reviewer's label. It never changes pass/fail, and identical failures of this case reuse it. */
function Review({ run, token, onReviewed }: { run: Run; token: string; onReviewed: (run: Run) => void }) {
  const [label, setLabel] = useState<TriageLabel>(run.triage?.label ?? "real_regression");
  const [text, setText] = useState(run.triage?.engine === "human" ? run.triage.explanation : "");
  const [state, setState] = useState<string>("idle");

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
    <div>
      <div className="small muted" style={{ marginTop: 6 }}>Change the label</div>
      <div className="review">
        <select value={label} onChange={(e) => setLabel(e.target.value as TriageLabel)} aria-label="Label">
          {LABELS.map((l) => <option key={l.value} value={l.value}>{l.text}</option>)}
        </select>
        <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Reason"
          placeholder="Reason, shown in the sign-off document" />
        <button className="btn" disabled={text.trim().length < 10 || state === "saving"} onClick={save}>
          {state === "saving" ? "Saving" : "Save label"}
        </button>
      </div>
      {state === "saved" && <div className="small muted" style={{ marginTop: 6 }}>Saved. Identical failures of this case will reuse it.</div>}
      {!["idle", "saving", "saved"].includes(state) && <div className="small error" style={{ marginTop: 6 }}>{state}</div>}
    </div>
  );
}
