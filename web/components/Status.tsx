import type { Status, TriageLabel } from "@/lib/types";

// Status colours are reserved for state and always ship with an icon + text label.
export const OUTCOMES = {
  pass: { label: "Passed", color: "var(--good)", icon: "✓" },
  real_regression: { label: "Regression", color: "var(--critical)", icon: "✕" },
  flaky: { label: "Flaky", color: "var(--warning)", icon: "~" },
  environment_issue: { label: "Environment", color: "var(--serious)", icon: "!" },
  untriaged: { label: "Failed", color: "var(--critical)", icon: "✕" },
  not_run: { label: "Not run", color: "var(--axis)", icon: "–" },
} as const;

export type Outcome = keyof typeof OUTCOMES;

export function outcomeOf(status: Status | null | undefined, triage: TriageLabel | null | undefined): Outcome {
  if (!status) return "not_run";
  if (status === "pass") return "pass";
  return triage ?? "untriaged";
}

export function StatusBadge({ outcome }: { outcome: Outcome }) {
  const o = OUTCOMES[outcome];
  return (
    <span className="badge">
      <span className="dot" style={{ background: o.color }} aria-hidden />
      <span aria-hidden>{o.icon}</span>
      {outcome === "pass" || outcome === "not_run" ? o.label : `Fail · ${o.label}`}
    </span>
  );
}
