from conftest import FakeClient, message, text, tool_use

from uatpilot.generator import Generator
from uatpilot.models import TargetKind

GOOD_CASE = {
    "title": "Ping returns success",
    "requirement_ref": "AC-1.1",
    "preconditions": [],
    "steps": ["Send GET /ping"],
    "expected_result": "A 2xx status.",
    "priority": "high",
    "executable": {
        "kind": "api",
        "steps": [
            {
                "name": "ping",
                "method": "GET",
                "path": "/ping",
                "assertions": [{"source": "status_code", "op": "less_than", "expected": 300}],
            }
        ],
    },
}


def gen(client, settings, kind=TargetKind.API):
    return Generator(
        base_url="https://restful-booker.herokuapp.com",
        target_kind=kind,
        client=client,
        settings=settings,
    )


def test_invalid_suite_is_bounced_back_with_errors(settings):
    bad = {**GOOD_CASE, "priority": "urgent"}
    client = FakeClient(
        [
            message(
                text("Writing the suite."), tool_use("submit_test_cases", {"test_cases": [bad]})
            ),
            message(tool_use("submit_test_cases", {"test_cases": [GOOD_CASE]}, id="tu_2")),
        ]
    )
    g = gen(client, settings)
    events = list(g.generate("AC-1.1 ping works", "req.md"))
    subs = [e for e in events if e["type"] == "submission"]
    assert [s["accepted"] for s in subs] == [False, True]
    assert "priority" in subs[0]["detail"]
    assert g.suite is not None and len(g.suite.test_cases) == 1
    # requirements are wrapped and the target is stated
    assert "<requirements>" in client.requests[0]["messages"][0]["content"]
    tools = [t["name"] for t in client.requests[0]["tools"]]
    assert tools == ["http_probe", "submit_test_cases"]


def test_wrong_target_kind_rejected(settings):
    ui_case = {
        **GOOD_CASE,
        "executable": {
            "kind": "ui",
            "steps": [{"action": "goto", "value": "/"}, {"action": "expect_url", "value": "/"}],
        },
    }
    client = FakeClient(
        [
            message(tool_use("submit_test_cases", {"test_cases": [ui_case]})),
            message(tool_use("submit_test_cases", {"test_cases": [GOOD_CASE]}, id="tu_2")),
        ]
    )
    g = gen(client, settings)
    events = list(g.generate("x", "req.md"))
    assert "api target" in next(e for e in events if e["type"] == "submission")["detail"]


def test_probe_budget_enforced(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "uatpilot.generator.probe",
        lambda base, path, s: calls.append(path) or {"status_code": 200, "body": {}},
    )
    probes = [tool_use("http_probe", {"path": f"/p{i}"}, id=f"tu_{i}") for i in range(10)]
    client = FakeClient(
        [message(*probes), message(tool_use("submit_test_cases", {"test_cases": [GOOD_CASE]}))]
    )
    g = gen(client, settings)
    events = list(g.generate("x", "req.md"))
    assert len(calls) == settings.max_probes
    errors = [e for e in events if e["type"] == "tool_result" and e["is_error"]]
    assert len(errors) == 10 - settings.max_probes


def test_ui_target_offers_inspect_page(settings):
    client = FakeClient(
        [
            message(
                tool_use(
                    "submit_test_cases",
                    {
                        "test_cases": [
                            {
                                **GOOD_CASE,
                                "executable": {
                                    "kind": "ui",
                                    "steps": [
                                        {"action": "goto", "value": "/"},
                                        {"action": "expect_visible", "selector": "#x"},
                                    ],
                                },
                            }
                        ]
                    },
                )
            )
        ]
    )
    g = gen(client, settings, TargetKind.UI)
    list(g.generate("x", "req.md"))
    assert [t["name"] for t in client.requests[0]["tools"]] == [
        "inspect_page",
        "submit_test_cases",
    ]


def test_model_that_never_submits_raises(settings):
    import pytest

    from uatpilot.llm import AgentError

    client = FakeClient([message(text("hmm")), message(text("still thinking"))])
    with pytest.raises(AgentError, match="submit_test_cases"):
        list(gen(client, settings).generate("x", "req.md"))
