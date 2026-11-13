"""Failure triage: an immediate re-run, deterministic signals, reuse of earlier decisions, then
the triage agent.

The verdict floor (same idea as scam-checker-agent's): the triage label is metadata attached to a
failed run. Nothing here can turn a failed run into a pass, and the label is clamped to what the
evidence supports:
  * "flaky" only if the immediate re-run passed, or the case's recent history alternates between
    pass and fail (Allure's history rule);
  * "environment_issue" only if the evidence contains an environment signal (5xx, timeout,
    connection failure, 429, auth expiry);
  * "test_defect" only if the evidence contains a test-defect signal (an unresolved {{variable}},
    an assertion that could not be evaluated, an invalid selector);
  * anything else becomes "real_regression", with `floor_applied` recording what was claimed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from uatpilot.config import Settings, get_settings
from uatpilot.llm import AgentError, Event, Tool, ToolLoop, ToolOutcome
from uatpilot.models import TestCase, Triage, TriageDecision, TriageLabel
from uatpilot.prompts import TRIAGE_NUDGE, TRIAGE_SYSTEM
from uatpilot.target import probe


def environment_signals(evidence: dict[str, Any]) -> list[str]:
    """Hard facts in the evidence that point at the environment rather than the behaviour."""
    signals: list[str] = []
    if err := evidence.get("error"):
        signals.append(f"runner error: {err.get('kind')}")
    for step in evidence.get("steps", []):
        name = step.get("name") or step.get("action") or "step"
        if err := step.get("error"):
            if err.get("kind") in ("timeout", "connection"):
                signals.append(f"{name}: {err['kind']} ({str(err.get('detail', ''))[:120]})")
        status = (step.get("response") or {}).get("status_code")
        if not isinstance(status, int):
            continue
        asserted = {
            a.get("expected")
            for a in step.get("assertions", [])
            if a.get("source") == "status_code"
        }
        if status in asserted or str(status) in asserted:
            continue  # the test expected this status: it's behaviour, not environment
        if status >= 500:
            signals.append(f"{name}: HTTP {status} server error")
        elif status == 429:
            signals.append(f"{name}: HTTP 429 rate limited")
        elif status in (401, 403) and step.get("name", "").lower().startswith("auth"):
            signals.append(f"{name}: HTTP {status} on the auth step (credentials/token expired?)")
    return signals


_BAD_SELECTOR = re.compile(
    r"(not a valid selector|Unexpected token|selector.*(parse|syntax))", re.I
)


def broken_test_signals(evidence: dict[str, Any]) -> list[str]:
    """Hard facts that the *test* is broken, not the system (Allure BROKEN / RP Automation Bug)."""
    signals: list[str] = []
    for step in evidence.get("steps", []):
        name = step.get("name") or step.get("action") or "step"
        if missing := step.get("unresolved_vars"):
            signals.append(f"{name}: unresolved variable(s) {', '.join(missing)}")
        for a in step.get("assertions", []):
            if a.get("error"):
                signals.append(f"{name}: assertion could not be evaluated ({a['error'][:100]})")
        err = step.get("error") or {}
        if err.get("kind") == "browser" and _BAD_SELECTOR.search(str(err.get("detail", ""))):
            signals.append(f"{name}: invalid selector ({str(err['detail'])[:100]})")
    return signals


def history_flaky(statuses: list[str]) -> bool:
    """Allure's rule, on this case's previous results (newest first, up to 5): flaky if there is
    a pass that is more recent than a failure, i.e. the case alternates instead of staying red."""
    recent = statuses[:5]
    if "pass" not in recent or "fail" not in recent:
        return False
    return recent.index("pass") < len(recent) - 1 - recent[::-1].index("fail")


@dataclass
class Signals:
    rerun_passed: bool
    env: list[str] = field(default_factory=list)
    test_defect: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)  # previous statuses, newest first

    @property
    def history_flaky(self) -> bool:
        return history_flaky(self.history)


def apply_triage_floor(decision: TriageDecision, signals: Signals, engine: str) -> Triage:
    label, reasons = decision.label, []
    if label is TriageLabel.FLAKY and not (signals.rerun_passed or signals.history_flaky):
        reasons.append(
            "flaky claimed, but the re-run failed too and the history is not alternating"
        )
    if label is TriageLabel.ENVIRONMENT_ISSUE and not signals.env:
        reasons.append("environment_issue claimed, but the evidence has no environment signal")
    if label is TriageLabel.TEST_DEFECT and not signals.test_defect:
        reasons.append("test_defect claimed, but the evidence has no test-defect signal")
    if reasons:
        return Triage(
            label=TriageLabel.REAL_REGRESSION,
            explanation=decision.explanation,
            confidence="low",
            engine=engine,  # type: ignore[arg-type]
            floor_applied={"from": label.value, "reasons": reasons},
        )
    return Triage(
        label=label,
        explanation=decision.explanation,
        confidence=decision.confidence,
        engine=engine,  # type: ignore[arg-type]
    )


_NUMBERS = re.compile(r"\d+")


def failure_signature(case_id: str, evidence: dict[str, Any]) -> str:
    """Case + first failure with numbers masked, so 'expected 204, got 201 for booking 5512' and
    the same failure for booking 5590 share a signature (ReportPortal's same-test-case match)."""
    return f"{case_id}|{_NUMBERS.sub('#', first_failure(evidence))}"


def reuse_triage(previous: dict[str, Any] | None, signals: Signals) -> Triage | None:
    """Reuse an earlier decision for an identical failure of the same case, re-checked against
    *today's* evidence by the same floor. Human decisions win over model ones (the caller orders
    them); flaky is never reused, because it is a statement about one particular run."""
    if not previous or previous.get("label") == TriageLabel.FLAKY.value:
        return None
    decision = TriageDecision(
        label=previous["label"],
        explanation=previous["explanation"],
        confidence=previous.get("confidence", "medium"),
    )
    t = apply_triage_floor(decision, signals, engine="reused")
    if t.floor_applied:
        return None  # today's evidence no longer supports it: decide afresh
    who = "a reviewer" if previous.get("engine") == "human" else "an earlier triage"
    t.explanation = (
        f"{decision.explanation} (Same failure as run {previous['run_id']}, labelled by {who}.)"
    )
    t.reused_from = previous["run_id"]
    return t


def first_failure(evidence: dict[str, Any]) -> str:
    for step in evidence.get("steps", []):
        if err := step.get("error"):
            return f"step '{step.get('name') or step.get('action')}' hit a {err.get('kind')}: {err.get('detail')}"
        if failure := step.get("failure"):
            return failure
        for a in step.get("assertions", []):
            if not a.get("passed"):
                what = a["source"] + (f" {a['path']}" if a.get("path") else "")
                return (
                    f"step '{step.get('name')}': expected {what} {a['op']} {a.get('expected')!r}, "
                    f"got {a.get('actual')!r}"
                )
    if err := evidence.get("error"):
        return f"{err.get('kind')}: {err.get('detail')}"
    return "the run failed without a recorded assertion"


def rerun_first_failure(evidence: dict[str, Any]) -> str | None:
    return (evidence.get("rerun") or {}).get("first_failure")


def rules_triage(evidence: dict[str, Any], signals: Signals) -> Triage:
    """Deterministic fallback when no model is available. Same floor, by construction."""
    what = first_failure(evidence)
    rerun_passed, env = signals.rerun_passed, signals.env
    if signals.test_defect:
        return Triage(
            label=TriageLabel.TEST_DEFECT,
            explanation=f"The test itself is broken: {signals.test_defect[0]}.",
            confidence="medium",
            engine="rules",
        )
    reproduced = not rerun_passed and rerun_first_failure(evidence) == what
    # 502/503/504 mean "unavailable / gateway": environment even when they repeat.
    only_server_errors = env and all(
        "server error" in s and not re.search(r"HTTP 50[234]\b", s) for s in env
    )
    if only_server_errors and reproduced:
        # The same 5xx at the same step twice in a row, while earlier steps on the same host
        # worked, is the system failing on that input: a defect, not a flaky environment.
        return Triage(
            label=TriageLabel.REAL_REGRESSION,
            explanation=f"Server error reproduced on an immediate re-run: {what}.",
            confidence="medium",
            engine="rules",
        )
    if env:
        return Triage(
            label=TriageLabel.ENVIRONMENT_ISSUE,
            explanation=f"Environment problem, not the behaviour under test: {env[0]}.",
            confidence="medium",
            engine="rules",
        )
    if rerun_passed:
        return Triage(
            label=TriageLabel.FLAKY,
            explanation=f"Failed once ({what}) but passed on an immediate re-run.",
            confidence="medium",
            engine="rules",
        )
    if signals.history_flaky:
        return Triage(
            label=TriageLabel.FLAKY,
            explanation=f"Failed twice now ({what}), but this case has alternated between pass "
            "and fail in its recent runs.",
            confidence="low",
            engine="rules",
        )
    return Triage(
        label=TriageLabel.REAL_REGRESSION,
        explanation=f"Failed on two consecutive runs: {what}.",
        confidence="medium",
        engine="rules",
    )


class TriageAgent:
    def __init__(
        self,
        *,
        base_url: str,
        client: Any = None,  # injected SDK client (tests); built from settings otherwise
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = base_url
        self.client = client
        self._health_checked = False

    def _health(self, _args: dict[str, Any]) -> ToolOutcome:
        if self._health_checked:
            return ToolOutcome("Already checked once for this failure.", is_error=True)
        self._health_checked = True
        try:
            r = probe(self.base_url, "/", self.settings)
            return ToolOutcome(json.dumps({k: r[k] for k in ("status_code", "elapsed_ms")}))
        except Exception as exc:
            return ToolOutcome(f"Target unreachable: {type(exc).__name__}: {exc}")

    @staticmethod
    def _submit(args: dict[str, Any]) -> ToolOutcome:
        try:
            d = TriageDecision.model_validate(args)
        except ValidationError as exc:
            return ToolOutcome(f"Rejected: {exc.errors()[:5]}", is_error=True)
        return ToolOutcome("Recorded.", accepted=d)

    def triage(
        self, case: TestCase, evidence: dict[str, Any], rerun: dict[str, Any], signals: Signals
    ) -> Iterator[Event]:
        """Yields trace events; the last is {"type": "triage", "triage": Triage}."""
        self._health_checked = False
        loop = ToolLoop(
            client=self.client,
            settings=self.settings,
            system=TRIAGE_SYSTEM,
            tools=[
                Tool(
                    name="check_target_health",
                    description="One GET to the target's root to see if it is up right now.",
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    handler=self._health,
                ),
                Tool(
                    name="submit_triage",
                    description="Submit the classification for this failure.",
                    input_schema=TriageDecision.model_json_schema(),
                    handler=self._submit,
                ),
            ],
            submit_tool="submit_triage",
            nudge=TRIAGE_NUDGE,
            max_tokens=8000,
        )
        payload = {
            "test_case": case.model_dump(mode="json", exclude={"executable"}),
            "executable_check": case.executable.model_dump(mode="json"),
            "failing_run_evidence": evidence,
            "first_failure": first_failure(evidence),
            "immediate_rerun": {
                "passed": rerun["passed"],
                "first_failure": rerun.get("first_failure"),
            },
            "environment_signals_detected_by_code": signals.env,
            "test_defect_signals_detected_by_code": signals.test_defect,
            "previous_statuses_newest_first": signals.history[:10],
        }
        yield from loop.run(
            "<failure_report>\n" + json.dumps(payload, default=str)[:60000] + "\n</failure_report>"
        )
        triage = apply_triage_floor(loop.result, signals, "llm")
        triage.model = loop.backend.label
        yield {"type": "triage", "triage": triage}


def build_signals(
    evidence: dict[str, Any], rerun: dict[str, Any], history: list[str] | None = None
) -> Signals:
    return Signals(
        rerun_passed=rerun["passed"],
        env=environment_signals(evidence),
        test_defect=broken_test_signals(evidence),
        history=history or [],
    )


def triage_or_fallback(
    agent: TriageAgent | None,
    case: TestCase,
    evidence: dict[str, Any],
    rerun: dict[str, Any],
    history: list[str] | None = None,
    previous: dict[str, Any] | None = None,
) -> Iterator[Event]:
    """`history`: this case's earlier statuses, newest first. `previous`: the best earlier
    decision for the same failure signature (see Store.reusable_triage), if any."""
    signals = build_signals(evidence, rerun, history)
    if reused := reuse_triage(previous, signals):
        yield {"type": "triage", "triage": reused}
        return
    if agent is not None:
        try:
            yield from agent.triage(case, evidence, rerun, signals)
            return
        except AgentError as exc:
            yield {"type": "warning", "message": f"Triage agent unavailable ({exc}); used rules."}
    yield {"type": "triage", "triage": rules_triage(evidence, signals)}
