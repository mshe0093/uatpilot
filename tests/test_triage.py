from conftest import FakeClient, message, tool_use

from uatpilot.models import TestCase, TriageDecision, TriageLabel
from uatpilot.triage import (
    TriageAgent,
    apply_triage_floor,
    environment_signals,
    rules_triage,
    triage_or_fallback,
)


def decision(label, conf="high"):
    return TriageDecision(
        label=label, explanation="Something specific went wrong.", confidence=conf
    )


# ---- the verdict floor ------------------------------------------------------------------------
def test_flaky_requires_a_passing_rerun():
    t = apply_triage_floor(decision("flaky"), rerun_passed=False, env_signals=[], engine="claude")
    assert t.label is TriageLabel.REAL_REGRESSION
    assert t.floor_applied["from"] == "flaky"


def test_flaky_allowed_when_rerun_passed():
    t = apply_triage_floor(decision("flaky"), rerun_passed=True, env_signals=[], engine="claude")
    assert t.label is TriageLabel.FLAKY and t.floor_applied is None


def test_environment_issue_requires_a_signal():
    t = apply_triage_floor(
        decision("environment_issue"), rerun_passed=False, env_signals=[], engine="claude"
    )
    assert t.label is TriageLabel.REAL_REGRESSION
    t = apply_triage_floor(
        decision("environment_issue"),
        rerun_passed=False,
        env_signals=["x: timeout"],
        engine="claude",
    )
    assert t.label is TriageLabel.ENVIRONMENT_ISSUE


# ---- deterministic signals ----------------------------------------------------------------------
def step(status, expected=200, name="call"):
    return {
        "name": name,
        "response": {"status_code": status},
        "assertions": [
            {
                "source": "status_code",
                "op": "equals",
                "expected": expected,
                "passed": status == expected,
                "actual": status,
            }
        ],
    }


def test_environment_signals():
    assert environment_signals({"steps": [step(503)]})
    assert environment_signals({"steps": [step(429)]})
    assert environment_signals({"steps": [{"name": "x", "error": {"kind": "timeout"}}]})
    assert environment_signals({"steps": [step(401, name="auth step")]})
    assert not environment_signals({"steps": [step(404)]})
    # The test asserted a 500 on purpose: that's behaviour, not environment.
    assert not environment_signals({"steps": [step(500, expected=500)]})


def test_rules_triage_paths():
    ev = {"steps": [step(404)]}
    assert rules_triage(ev, True, []).label is TriageLabel.FLAKY
    assert rules_triage(ev, False, []).label is TriageLabel.REAL_REGRESSION
    timeout = {"steps": [{"name": "x", "error": {"kind": "timeout", "detail": "t"}}]}
    assert rules_triage(timeout, False, ["x: timeout"]).label is TriageLabel.ENVIRONMENT_ISSUE


def test_repeated_503_stays_environment():
    from uatpilot.triage import first_failure

    ev = {"steps": [step(503)]}
    ev["rerun"] = {"passed": False, "first_failure": first_failure(ev)}
    assert rules_triage(ev, False, environment_signals(ev)).label is TriageLabel.ENVIRONMENT_ISSUE


def test_reproduced_server_error_is_a_defect():
    ev = {"steps": [step(500, expected=400)]}
    from uatpilot.triage import first_failure

    ev["rerun"] = {"passed": False, "first_failure": first_failure(ev)}
    env = environment_signals(ev)
    assert env
    assert rules_triage(ev, False, env).label is TriageLabel.REAL_REGRESSION


# ---- the agent, with a scripted model ------------------------------------------------------------
CASE = TestCase.model_validate(
    {
        "id": "rs-TC001",
        "requirement_set_id": "rs",
        "title": "Ping returns success",
        "requirement_ref": "AC-1.1",
        "steps": ["GET /ping"],
        "expected_result": "2xx",
        "priority": "high",
        "executable": {
            "kind": "api",
            "steps": [
                {
                    "name": "ping",
                    "method": "GET",
                    "path": "/ping",
                    "assertions": [{"source": "status_code", "op": "equals", "expected": 200}],
                }
            ],
        },
    }
)


def test_agent_label_is_clamped_by_floor(settings):
    client = FakeClient(
        [
            message(
                tool_use(
                    "submit_triage",
                    {"label": "flaky", "explanation": "Looks flaky to me.", "confidence": "high"},
                )
            )
        ]
    )
    agent = TriageAgent(
        base_url="https://restful-booker.herokuapp.com", client=client, settings=settings
    )
    events = list(triage_or_fallback(agent, CASE, {"steps": [step(404)]}, {"passed": False}))
    t = events[-1]["triage"]
    assert t.label is TriageLabel.REAL_REGRESSION and t.engine == "claude"
    assert t.floor_applied["reasons"]
    # the request carried the evidence and the code's own environment verdict
    sent = client.requests[0]["messages"][0]["content"]
    assert "environment_signals_detected_by_code" in sent


def test_agent_invalid_submission_gets_feedback_then_accepted(settings):
    client = FakeClient(
        [
            message(
                tool_use(
                    "submit_triage", {"label": "maybe", "explanation": "x", "confidence": "high"}
                )
            ),
            message(
                tool_use(
                    "submit_triage",
                    {
                        "label": "real_regression",
                        "explanation": "Expected 200, got 404 on ping.",
                        "confidence": "medium",
                    },
                    id="tu_2",
                )
            ),
        ]
    )
    agent = TriageAgent(
        base_url="https://restful-booker.herokuapp.com", client=client, settings=settings
    )
    events = list(triage_or_fallback(agent, CASE, {"steps": [step(404)]}, {"passed": False}))
    assert [e["accepted"] for e in events if e["type"] == "submission"] == [False, True]
    assert events[-1]["triage"].label is TriageLabel.REAL_REGRESSION
    # the second request includes the rejection as an error tool_result
    second = client.requests[1]["messages"][-1]["content"][0]
    assert second["is_error"] is True


def test_agent_failure_falls_back_to_rules(settings):
    client = FakeClient([message(stop_reason="refusal")])
    agent = TriageAgent(
        base_url="https://restful-booker.herokuapp.com", client=client, settings=settings
    )
    events = list(triage_or_fallback(agent, CASE, {"steps": [step(404)]}, {"passed": True}))
    assert events[0]["type"] == "warning"
    assert events[-1]["triage"].engine == "rules"
    assert events[-1]["triage"].label is TriageLabel.FLAKY
