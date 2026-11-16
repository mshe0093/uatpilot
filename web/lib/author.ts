import type { RequirementSetSummary } from "./types";

/** Who wrote a suite, short enough for a sidebar: "hand-written" or the model, e.g. "gemini-3.5-flash". */
export function author(s: Pick<RequirementSetSummary, "generated_by">): string {
  if (!s.generated_by || s.generated_by.startsWith("import:")) return "hand-written";
  return s.generated_by.split(":").slice(1).join(":") || s.generated_by;
}

/** A suite's display name: its requirements file without the extension. */
export function suiteName(s: Pick<RequirementSetSummary, "source_name">): string {
  return s.source_name.replace(/\.md$/, "");
}
