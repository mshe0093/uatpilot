export type Status = "pass" | "fail";
export type TriageLabel = "real_regression" | "flaky" | "environment_issue";

export interface Health {
  status: string;
  version: string;
  claude_available: boolean;
  generation_available: boolean;
  read_only: boolean;
  allowed_hosts: string[];
}

export interface RequirementSetSummary {
  id: string;
  source_name: string;
  target_kind: "api" | "ui";
  base_url: string;
  generated_by: string;
  created_at: string;
  case_count: number;
}

export interface Triage {
  label: TriageLabel;
  explanation: string;
  confidence: string;
  engine: "claude" | "rules";
  floor_applied?: { from: string; reasons: string[] } | null;
}

export interface TestCase {
  id: string;
  title: string;
  requirement_ref: string;
  preconditions: string[];
  steps: string[];
  expected_result: string;
  priority: "high" | "medium" | "low";
  executable: { kind: "api" | "ui"; steps: Record<string, unknown>[] };
  latest_run: { status: Status; triage_label: TriageLabel | null; run_at: string } | null;
}

export interface Stats {
  runs: number;
  failed: number;
  flaky: number;
  flaky_rate: number;
  pass_rate: number;
}

export interface RequirementSet extends Omit<RequirementSetSummary, "case_count"> {
  raw_text: string;
  test_cases: TestCase[];
  stats: Stats;
}

export interface Batch {
  id: string;
  requirement_set_id: string;
  source_name: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  passed: number;
  failed: number;
  regressions: number;
  flaky: number;
  env_issues: number;
}

export interface Run {
  id: string;
  test_case_id: string;
  status: Status;
  duration_ms: number;
  run_at: string;
  evidence: Record<string, unknown>;
  triage: Triage | null;
  title?: string;
  requirement_ref?: string;
}

export type StreamEvent =
  | { type: "batch_start"; batch_id: string; total: number; base_url: string; triage_engine: string }
  | { type: "case_start"; test_case_id: string; title: string }
  | { type: "rerun"; test_case_id: string; passed: boolean }
  | { type: "case_result"; run: Run; title: string }
  | { type: "batch_done"; batch_id: string; pass: number; fail: number }
  | { type: "start"; source_name: string; target: string }
  | { type: "text"; text: string }
  | { type: "tool_call"; id: string; name: string; input: unknown }
  | { type: "tool_result"; id: string; name: string; is_error: boolean; content: string }
  | { type: "submission"; accepted: boolean; detail: string }
  | { type: "usage"; usage: Record<string, number> }
  | { type: "saved"; requirement_set_id: string; case_count: number }
  | { type: "warning"; message: string }
  | { type: "error"; message: string };
