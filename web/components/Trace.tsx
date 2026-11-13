import type { StreamEvent } from "@/lib/types";
import { StatusBadge, outcomeOf } from "./Status";

/** The agent "showing its work": every tool call, result, submission and run result, live. */
export function Trace({ events }: { events: StreamEvent[] }) {
  return (
    <ul className="trace" aria-live="polite">
      {events.map((ev, i) => <li key={i}>{line(ev)}</li>)}
    </ul>
  );
}

function line(ev: StreamEvent): React.ReactNode {
  switch (ev.type) {
    case "batch_start":
      return <span className="muted">Running {ev.total} cases against {ev.base_url} (triage: {ev.triage_engine})</span>;
    case "case_start":
      return <span className="muted">▶ {ev.title}</span>;
    case "rerun":
      return <span className="muted small">↻ immediate re-run {ev.passed ? "passed" : "failed again"}</span>;
    case "case_result":
      return (
        <span>
          <StatusBadge outcome={outcomeOf(ev.run.status, ev.run.triage?.label)} /> {ev.title}
          {ev.run.triage && <span className="muted small"> · {ev.run.triage.explanation}</span>}
        </span>
      );
    case "batch_done":
      return <strong>Done: {ev.pass} passed, {ev.fail} failed</strong>;
    case "start":
      return <span className="muted">Reading {ev.source_name} for {ev.target}</span>;
    case "text":
      return <span className="muted">{ev.text}</span>;
    case "tool_call":
      return <span className="mono">→ {ev.name} {JSON.stringify(ev.input).slice(0, 160)}</span>;
    case "tool_result":
      return <span className={`mono ${ev.is_error ? "error" : "muted"}`}>← {ev.content.slice(0, 220)}</span>;
    case "submission":
      return ev.accepted
        ? <strong>Suite accepted by the schema</strong>
        : <span className="error small">Suite rejected: {ev.detail.slice(0, 400)}</span>;
    case "usage":
      return <span className="muted small">{ev.usage.model_calls} model calls, {ev.usage.tool_calls} tool calls</span>;
    case "saved":
      return <strong>Saved {ev.case_count} test cases ({ev.requirement_set_id})</strong>;
    case "warning":
      return <span className="small" style={{ color: "var(--serious)" }}>{ev.message}</span>;
    case "error":
      return <span className="error">{ev.message}</span>;
  }
}
