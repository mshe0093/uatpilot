"use client";

import { useRef } from "react";
import type { Matrix, Run } from "@/lib/types";
import { OUTCOMES, outcomeOf, type Outcome } from "./Status";

/**
 * The signature view: test cases as rows, runs as columns (oldest left, newest right). A red tail
 * is a regression, a row that flickers is flaky, a row turning green is a fix. While a suite is
 * running, a pending column on the right fills cell by cell as results stream in.
 */
export function RunMatrix({ matrix, live, liveTotal, onOpen }: {
  matrix: Matrix;
  live: Record<string, Run> | null; // results of the run in progress, keyed by case id
  liveTotal: number;
  onOpen: (caseId: string, runId?: string) => void;
}) {
  const table = useRef<HTMLTableElement>(null);
  const columns = matrix.batches.length + (live ? 1 : 0);
  const latest = matrix.batches.length - 1;

  // Arrow keys move between cells (a grid, not one tab stop per cell).
  function onKey(e: React.KeyboardEvent<HTMLTableElement>) {
    const target = e.target as HTMLElement;
    const r = Number(target.dataset.r), c = Number(target.dataset.c);
    if (Number.isNaN(r)) return;
    const moves: Record<string, [number, number]> = {
      ArrowUp: [r - 1, c], ArrowDown: [r + 1, c], ArrowLeft: [r, c - 1], ArrowRight: [r, c + 1],
    };
    const next = moves[e.key];
    if (!next) return;
    const el = table.current?.querySelector<HTMLElement>(`[data-r="${next[0]}"][data-c="${next[1]}"]`);
    if (el) { e.preventDefault(); el.focus(); }
  }

  if (matrix.batches.length === 0 && !live) {
    return (
      <div className="empty">
        <strong>No runs yet</strong>
        <span>Run the suite to see each case's history build up here, one column per run.</span>
      </div>
    );
  }

  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="matrix-scroll">
        <table className="matrix" ref={table} onKeyDown={onKey} role="grid"
          aria-label={`Results of ${matrix.rows.length} test cases over ${columns} runs, oldest first`}>
          <thead>
            <tr>
              <th className="case"><span className="sr-only">Test case</span></th>
              {matrix.batches.map((b, i) => (
                <th key={b.id} className={i === latest && !live ? "latest" : ""} title={new Date(b.started_at).toLocaleString()}>
                  {i + 1}
                </th>
              ))}
              {live && <th className="latest">now</th>}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row, r) => (
              <tr key={row.case_id}>
                <th className="case" scope="row">
                  <button onClick={() => onOpen(row.case_id)}>
                    <span className="id mono">{row.case_id.split("-").pop()}</span>
                    <span className="title">{row.title}</span>
                  </button>
                </th>
                {row.cells.map((cell, c) => {
                  const outcome: Outcome = cell ? outcomeOf(cell.status, cell.label) : "not_run";
                  return (
                    <td key={c} className={`cell ${c === latest && !live ? "latest" : ""}`}>
                      <button className={cell ? outcome : ""} data-r={r} data-c={c} tabIndex={r === 0 && c === 0 ? 0 : -1}
                        aria-label={`${row.case_id.split("-").pop()}, run ${c + 1}: ${OUTCOMES[outcome].label}`}
                        title={`Run ${c + 1}: ${OUTCOMES[outcome].label}`}
                        onClick={() => cell && onOpen(row.case_id, cell.run_id)} />
                    </td>
                  );
                })}
                {live && (() => {
                  const run = live[row.case_id];
                  const outcome: Outcome = run ? outcomeOf(run.status, run.triage?.label) : "not_run";
                  return (
                    <td className="cell latest">
                      <button className={run ? `${outcome} fresh` : "pending"} data-r={r} data-c={columns - 1} tabIndex={-1}
                        aria-label={`${row.case_id.split("-").pop()}, current run: ${run ? OUTCOMES[outcome].label : "waiting"}`}
                        onClick={() => run && onOpen(row.case_id, run.id)} />
                    </td>
                  );
                })()}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="matrix-legend" aria-hidden>
        {(["pass", "real_regression", "flaky", "environment_issue", "test_defect"] as Outcome[]).map((o) => (
          <span key={o}><i className="swatch" style={{ background: OUTCOMES[o].color }} />{OUTCOMES[o].label}</span>
        ))}
        {live && <span>{Object.keys(live).length} of {liveTotal} done</span>}
      </div>
    </div>
  );
}
