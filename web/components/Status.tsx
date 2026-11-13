import { CircleCheck, CircleDashed, CircleX, CloudOff, Shuffle, Wrench, type LucideIcon } from "lucide-react";
import type { Status, TriageLabel } from "@/lib/types";

// Status colours are reserved for state and always ship with an icon and a word.
export const OUTCOMES = {
  pass: { label: "Passed", color: "var(--pass)", Icon: CircleCheck },
  real_regression: { label: "Regression", color: "var(--regression)", Icon: CircleX },
  flaky: { label: "Flaky", color: "var(--flaky)", Icon: Shuffle },
  environment_issue: { label: "Environment", color: "var(--environment)", Icon: CloudOff },
  test_defect: { label: "Test defect", color: "var(--test-defect)", Icon: Wrench },
  untriaged: { label: "Failed", color: "var(--regression)", Icon: CircleX },
  not_run: { label: "Not run", color: "var(--empty)", Icon: CircleDashed },
} satisfies Record<string, { label: string; color: string; Icon: LucideIcon }>;

export type Outcome = keyof typeof OUTCOMES;

export function outcomeOf(status: Status | null | undefined, triage: TriageLabel | null | undefined): Outcome {
  if (!status) return "not_run";
  if (status === "pass") return "pass";
  return triage ?? "untriaged";
}

export function StatusBadge({ outcome }: { outcome: Outcome }) {
  const { label, Icon } = OUTCOMES[outcome];
  return (
    <span className={`badge ${outcome}`}>
      <Icon size={15} strokeWidth={2.2} aria-hidden />
      {label}
    </span>
  );
}

const CHANGES: Record<string, string> = {
  new_failure: "New failure",
  fixed: "Fixed",
  still_failing: "Still failing",
};

/** Allure-style change against the previous run; only the ones worth reading are shown. */
export function ChangeTag({ change }: { change?: string }) {
  if (!change || !(change in CHANGES)) return null;
  return <span className={`change change-${change}`}>{CHANGES[change]}</span>;
}
