import { CircleCheck, CircleDashed, CircleX } from "lucide-react";
import type { Batch, RequirementSet } from "@/lib/types";

/** The question a release manager opens the page with: can we sign this off? */
export function Verdict({ detail, last }: { detail: RequirementSet; last?: Batch }) {
  const trace = detail.traceability;
  const failing = trace.filter((t) => t.result.startsWith("FAIL")).length;
  const missing = trace.filter((t) => t.result === "MISSING").length;
  const notRun = trace.filter((t) => t.result === "NOT RUN").length;

  let state: "ready" | "blocked" | "unknown";
  let headline: string;
  let detailLine: string;
  if (!last || last.total === 0) {
    state = "unknown";
    headline = "Not run yet.";
    detailLine = "Run the suite to find out whether this release can be signed off.";
  } else if (failing > 0) {
    state = "blocked";
    headline = "Not ready to sign off.";
    detailLine = `${failing} of ${trace.length} acceptance criteria are failing${missing ? `, and ${missing} have no test case` : ""}.`;
  } else if (missing > 0 || notRun > 0) {
    state = "unknown";
    headline = "Can’t sign off yet.";
    detailLine = missing
      ? `${missing} acceptance ${missing === 1 ? "criterion has" : "criteria have"} no test case.`
      : `${notRun} criteria haven’t been run since their cases changed.`;
  } else {
    state = "ready";
    headline = "Ready to sign off.";
    detailLine = trace.length
      ? `All ${trace.length} acceptance criteria pass.`
      : "Every test case passed in the latest run.";
  }
  const Icon = state === "ready" ? CircleCheck : state === "blocked" ? CircleX : CircleDashed;

  return (
    <section className="verdict" aria-live="polite">
      <div className="verdict-line">
        <span className={`verdict-icon ${state}`}><Icon size={18} strokeWidth={2.4} aria-hidden /></span>
        <h2>{headline}</h2>
      </div>
      <p>{detailLine}</p>
      {last && last.total > 0 && (
        <div className="facts">
          <span><strong>{last.passed}/{last.total}</strong> cases passed in the latest run</span>
          <span><strong>{last.regressions}</strong> regressions</span>
          <span><strong>{last.flaky}</strong> flaky</span>
          <span><strong>{last.env_issues + last.test_defects}</strong> environment or test problems</span>
          <span>Run {new Date(last.started_at).toLocaleString()}</span>
        </div>
      )}
    </section>
  );
}
