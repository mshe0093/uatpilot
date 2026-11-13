import type { Status, TriageLabel } from "@/lib/types";

// Status colours are reserved for state and always ship with an icon + text label.
export const OUTCOMES = {
  pass: { label: "Passed", color: "var(--good)", icon: "✓" },
  real_regression: { label: "Regression", color: "var(--critical)", icon: "✕" },
  flaky: { label: "Flaky", color: "var(--warning)", icon: "~" },
  environment_issue: { label: "Environment", color: "var(--serious)", icon: "!" },
  // Not one of the four reserved status hues: a broken test says nothing about the product.
  test_defect: { label: "Test defect", color: "var(--neutral)", icon: "⚙" },
  untriaged: { label: "Failed", color: "var(--critical)", icon: "✕" },
  not_run: { label: "Not run", color: "var(--axis)", icon: "–" },
} as const;

export type Outcome = keyof typeof OUTCOMES;

export function outcomeOf(status: Status | null | undefined, triage: TriageLabel | null | undefined): Outcome {
  if (!status) return "not_run";
  if (status === "pass") return "pass";
  return triage ?? "untriaged";
}

const CHANGES: Record<string, string> = {
  new_failure: "New failure",
  fixed: "Fixed",
  still_failing: "Still failing",
};

/** Allure-style change against the previous run; only the interesting ones are shown. */
export function ChangeTag({ change }: { change?: string }) {
  if (!change || !(change in CHANGES)) return null;
  return <span className={`change change-${change}`}>{CHANGES[change]}</span>;
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
