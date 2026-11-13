"""Features added after reviewing Schemathesis, qodo-cover, Allure, ReportPortal and hurl/stepci."""

import json
import sqlite3
from xml.etree import ElementTree as ET

import httpx
import pytest
from conftest import FakeClient, message, tool_use
from fastapi.testclient import TestClient
from pydantic import ValidationError

from uatpilot import pipeline
from uatpilot.api import create_app
from uatpilot.db import Store
from uatpilot.export import to_junit, to_markdown, traceability
from uatpilot.generator import Generator
from uatpilot.models import ApiSpec, Assertion, TargetKind, TestSuite, TriageDecision, TriageLabel
from uatpilot.requirements import condense_openapi, coverage, extract_criteria, parse_spec
from uatpilot.runner.api import check, run_api_spec
from uatpilot.target import make_client
from uatpilot.triage import (
    Signals,
    apply_triage_floor,
    broken_test_signals,
    failure_signature,
    history_flaky,
    reuse_triage,
    rules_triage,
)

BASE = "https://restful-booker.herokuapp.com"


def api_case(ref="AC-1.1", path="/ping", expected=200):
    return {
        "title": f"Case for {ref}",
        "requirement_ref": ref,
        "steps": ["do it"],
        "expected_result": "it works",
        "priority": "high",
        "executable": {
            "kind": "api",
            "steps": [
                {
                    "name": "call",
                    "method": "GET",
                    "path": path,
                    "assertions": [
                        {"source": "status_code", "op": "equals", "expected": expected}
                    ],
                }
            ],
        },
    }


# ---- 1. matches_schema (Schemathesis response conformance) ---------------------------------------
BOOKING_SCHEMA = {
    "type": "object",
    "required": ["firstname", "totalprice"],
    "properties": {"firstname": {"type": "string"}, "totalprice": {"type": "integer"}},
}


def test_matches_schema_pass_and_fail():
    a = Assertion(source="json", op="matches_schema", json_schema=BOOKING_SCHEMA)
    ok = check(a, httpx.Response(200, json={"firstname": "Ada", "totalprice": 5}), 1)
    assert ok["passed"] and "json_schema" not in ok  # evidence doesn't copy the schema
    bad = check(a, httpx.Response(200, json={"firstname": 3}), 1)
    assert not bad["passed"]
    assert any("totalprice" in e for e in bad["schema_errors"])
    assert any(e.startswith("firstname") for e in bad["schema_errors"])


def test_matches_schema_validation_rules():
    with pytest.raises(ValidationError, match="needs a json_schema"):
        Assertion(source="json", op="matches_schema")
    with pytest.raises(ValidationError, match="source 'json'"):
        Assertion(source="body_text", op="matches_schema", json_schema={"type": "string"})
    with pytest.raises(ValidationError, match="not a valid JSON Schema"):
        Assertion(source="json", op="matches_schema", json_schema={"type": 12})
    with pytest.raises(ValidationError, match="only used with"):
        Assertion(source="status_code", op="equals", expected=200, json_schema={"type": "x"})


# ---- 2. coverage gate + traceability (qodo-cover's coverage loop, for requirements) --------------
DOC = "## US-1\n- **AC-1.1** ping works\n- **AC-1.2** missing is 404\n- **NFR-1** fast\n"


def test_extract_criteria_dedupes_and_ignores_story_ids():
    assert extract_criteria(DOC + "see AC-1.1 again, US-2") == ["AC-1.1", "AC-1.2", "NFR-1"]


def test_coverage_report():
    suite = TestSuite.model_validate(
        {
            "test_cases": [api_case("AC-1.1, AC-1.2"), api_case("AC-9.9")],
            "not_covered": [{"requirement_ref": "NFR-1", "reason": "Needs load testing tools."}],
        }
    )
    r = coverage(extract_criteria(DOC), suite)
    assert r["covered"] == {"AC-1.1": [1], "AC-1.2": [1]}
    assert r["not_covered"] == {"NFR-1": "Needs load testing tools."}
    assert r["missing"] == [] and r["unknown_refs"] == ["AC-9.9"]


def test_generator_gate_bounces_missing_criteria(settings):
    client = FakeClient(
        [
            message(tool_use("submit_test_cases", {"test_cases": [api_case("AC-1.1")]})),
            message(
                tool_use(
                    "submit_test_cases",
                    {
                        "test_cases": [api_case("AC-1.1"), api_case("AC-1.2", "/booking/0", 404)],
                        "not_covered": [
                            {"requirement_ref": "NFR-1", "reason": "Not measurable from UAT."}
                        ],
                    },
                    id="tu_2",
                )
            ),
        ]
    )
    g = Generator(base_url=BASE, target_kind=TargetKind.API, client=client, settings=settings)
    events = list(g.generate(DOC, "req.md"))
    subs = [e for e in events if e["type"] == "submission"]
    assert [s["accepted"] for s in subs] == [False, True]
    assert "AC-1.2" in subs[0]["detail"] and "NFR-1" in subs[0]["detail"]
    assert g.coverage["not_covered"] == {"NFR-1": "Not measurable from UAT."}
    assert "AC-1.1, AC-1.2, NFR-1" in client.requests[0]["messages"][0]["content"]


def test_traceability_in_signoff(store, settings, monkeypatch):
    suite = TestSuite.model_validate(
        {"test_cases": [api_case("AC-1.1"), api_case("AC-1.2", "/missing", 404)]}
    )
    set_id = store.save_suite(
        source_name="req.md",
        raw_text=DOC,
        target_kind="api",
        base_url=BASE,
        generated_by="import:test",
        drafts=suite.test_cases,
        coverage={"not_covered": {}},
    )
    rows = {r["criterion"]: r["result"] for r in traceability(store, set_id)}
    assert rows == {"AC-1.1": "NOT RUN", "AC-1.2": "NOT RUN", "NFR-1": "MISSING"}
    _run_with(store, set_id, settings, monkeypatch, lambda req: httpx.Response(200))
    rows = {r["criterion"]: r["result"] for r in traceability(store, set_id)}
    assert rows["AC-1.1"] == "PASS" and rows["AC-1.2"].startswith("FAIL")
    md = to_markdown(store, set_id)
    assert "## Requirements traceability" in md and "| NFR-1 | - | MISSING |" in md


# ---- 3. OpenAPI input (the plan's MVP item 1) ------------------------------------------------------
OPENAPI_YAML = """
openapi: 3.0.0
info: {title: t, version: "1"}
paths:
  /booking/{id}:
    parameters: [{name: id, in: path, required: true, schema: {type: integer}}]
    get:
      summary: Get a booking
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Booking'}
        "404": {description: not found}
components:
  schemas:
    Booking:
      type: object
      properties: {firstname: {type: string}, example: {type: string}}
"""


def test_openapi_condensed_with_refs_resolved():
    ops = json.loads(condense_openapi(parse_spec(OPENAPI_YAML)))
    op = ops[0]
    assert op["op"] == "GET /booking/{id}" and op["parameters"][0]["name"] == "id"
    schema = op["responses"]["200"]
    assert schema["properties"]["firstname"]["type"] == "string"
    assert "example" in schema["properties"]  # a property named "example" survives
    assert op["responses"]["404"] == "not found"


def test_not_openapi_rejected():
    with pytest.raises(ValueError, match="Not an OpenAPI"):
        parse_spec('{"hello": 1}')


def test_openapi_goes_into_the_prompt(settings):
    client = FakeClient(
        [message(tool_use("submit_test_cases", {"test_cases": [api_case("AC-1.1")]}))]
    )
    g = Generator(base_url=BASE, target_kind=TargetKind.API, client=client, settings=settings)
    list(g.generate("AC-1.1 ping", "r.md", openapi=condense_openapi(parse_spec(OPENAPI_YAML))))
    assert "<openapi>" in client.requests[0]["messages"][0]["content"]


# ---- 4. history (Allure) ---------------------------------------------------------------------------
def test_history_flaky_rule():
    assert history_flaky(["pass", "fail"])  # passed more recently than it failed
    assert history_flaky(["fail", "pass", "fail"])
    assert not history_flaky(["fail", "fail", "pass"])  # was green, now consistently red
    assert not history_flaky(["pass", "pass"]) and not history_flaky([])


def test_floor_accepts_history_flaky():
    d = TriageDecision(label="flaky", explanation="It alternates a lot.", confidence="medium")
    t = apply_triage_floor(d, Signals(rerun_passed=False, history=["pass", "fail"]), "claude")
    assert t.label is TriageLabel.FLAKY and t.floor_applied is None
    ev = {
        "steps": [
            {
                "name": "s",
                "assertions": [
                    {
                        "source": "status_code",
                        "op": "equals",
                        "expected": 200,
                        "passed": False,
                        "actual": 404,
                    }
                ],
            }
        ]
    }
    assert rules_triage(ev, Signals(False, history=["pass", "fail"])).label is TriageLabel.FLAKY


def test_change_vs_previous_run(store, settings, monkeypatch):
    suite = TestSuite.model_validate({"test_cases": [api_case("AC-1.1")]})
    set_id = store.save_suite(
        source_name="r.md",
        raw_text="AC-1.1",
        target_kind="api",
        base_url=BASE,
        generated_by="import:t",
        drafts=suite.test_cases,
    )
    changes = []
    for status in (200, 500, 500, 200):
        batch = _run_with(
            store, set_id, settings, monkeypatch, lambda r, s=status: httpx.Response(s)
        )
        changes.append(store.batch_runs(batch)[0]["change"])
    assert changes == ["first_run", "new_failure", "still_failing", "fixed"]


# ---- 5. test_defect (Allure BROKEN / ReportPortal Automation Bug) ----------------------------------
def test_unresolved_variable_is_a_test_defect(settings):
    spec = ApiSpec.model_validate(
        {
            "kind": "api",
            "steps": [
                {
                    "name": "fetch",
                    "method": "GET",
                    "path": "/booking/{{never_captured}}",
                    "assertions": [{"source": "status_code", "op": "equals", "expected": 200}],
                }
            ],
        }
    )
    client = make_client(settings, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    passed, ev = run_api_spec(spec, BASE, settings, client)
    assert not passed and ev["steps"][0]["unresolved_vars"] == ["never_captured"]
    sig = broken_test_signals(ev)
    assert sig and "never_captured" in sig[0]
    assert rules_triage(ev, Signals(False, test_defect=sig)).label is TriageLabel.TEST_DEFECT


def test_floor_rejects_test_defect_without_signal():
    d = TriageDecision(label="test_defect", explanation="The test looks off.", confidence="high")
    t = apply_triage_floor(d, Signals(rerun_passed=False), "claude")
    assert t.label is TriageLabel.REAL_REGRESSION and t.floor_applied["from"] == "test_defect"


# ---- 6. reuse + human review (ReportPortal auto-analysis) -------------------------------------------
def test_signature_masks_numbers():
    ev1 = {
        "steps": [
            {
                "name": "get",
                "assertions": [
                    {
                        "source": "status_code",
                        "op": "equals",
                        "expected": 204,
                        "passed": False,
                        "actual": 201,
                    }
                ],
            }
        ]
    }
    ev2 = json.loads(json.dumps(ev1))
    ev2["steps"][0]["assertions"][0]["actual"] = 202
    assert failure_signature("c", ev1) == failure_signature("c", ev2)
    assert failure_signature("c", ev1) != failure_signature("d", ev1)


def test_reuse_rules():
    prev = {
        "label": "real_regression",
        "explanation": "Delete returns 201.",
        "confidence": "high",
        "engine": "human",
        "run_id": "run_1",
    }
    t = reuse_triage(prev, Signals(rerun_passed=False))
    assert t.engine == "reused" and t.reused_from == "run_1" and "reviewer" in t.explanation
    assert reuse_triage({**prev, "label": "flaky"}, Signals(rerun_passed=True)) is None
    # environment label no longer supported by today's evidence: decide afresh
    assert reuse_triage({**prev, "label": "environment_issue"}, Signals(False)) is None


def test_human_label_is_reused_and_skips_the_model(store, settings, monkeypatch):
    suite = TestSuite.model_validate({"test_cases": [api_case("AC-1.1", expected=204)]})
    set_id = store.save_suite(
        source_name="r.md",
        raw_text="AC-1.1",
        target_kind="api",
        base_url=BASE,
        generated_by="import:t",
        drafts=suite.test_cases,
    )
    b1 = _run_with(store, set_id, settings, monkeypatch, lambda r: httpx.Response(201))
    run1 = store.batch_runs(b1)[0]
    store.set_human_triage(run1["id"], "real_regression", "Spec says 204; the API returns 201.")
    assert store.batch_runs(b1)[0]["status"] == "fail"  # a human label never flips status
    # Second run: a triage *agent* is configured, but its client has no scripted replies, so a
    # model call would raise. The run must reuse the reviewer's decision instead.
    s2 = settings.__class__(**{**settings.__dict__, "engine": "claude"})
    b2 = _run_with(
        store, set_id, s2, monkeypatch, lambda r: httpx.Response(201), client=FakeClient([])
    )
    t = store.batch_runs(b2)[0]["triage"]
    assert t["engine"] == "reused" and t["reused_from"] == run1["id"]


def test_set_human_triage_refuses_passed_runs(store, settings, monkeypatch):
    suite = TestSuite.model_validate({"test_cases": [api_case("AC-1.1")]})
    set_id = store.save_suite(
        source_name="r.md",
        raw_text="AC-1.1",
        target_kind="api",
        base_url=BASE,
        generated_by="import:t",
        drafts=suite.test_cases,
    )
    b = _run_with(store, set_id, settings, monkeypatch, lambda r: httpx.Response(200))
    with pytest.raises(ValueError):
        store.set_human_triage(store.batch_runs(b)[0]["id"], "flaky", "Not actually flaky.")


def test_human_triage_endpoint(settings, monkeypatch):
    app = create_app(settings)
    store = app.state.store
    suite = TestSuite.model_validate({"test_cases": [api_case("AC-1.1")]})
    set_id = store.save_suite(
        source_name="r.md",
        raw_text="AC-1.1",
        target_kind="api",
        base_url=BASE,
        generated_by="import:t",
        drafts=suite.test_cases,
    )
    b = _run_with(store, set_id, settings, monkeypatch, lambda r: httpx.Response(500))
    run_id = store.batch_runs(b)[0]["id"]
    c = TestClient(app)
    r = c.put(
        f"/api/runs/{run_id}/triage",
        json={"label": "environment_issue", "explanation": "Sandbox was down for maintenance."},
    )
    assert r.status_code == 200 and r.json()["engine"] == "human"
    assert (
        c.put(
            "/api/runs/run_nope/triage", json={"label": "flaky", "explanation": "whatever it is."}
        ).status_code
        == 404
    )
    assert "traceability" in c.get(f"/api/requirement-sets/{set_id}").json()
    assert c.get(f"/api/requirement-sets/{set_id}/junit.xml").text.startswith("<?xml")


# ---- 7. JUnit XML (hurl / stepci / Allure interop) -----------------------------------------------------
def test_junit_export(store, settings, monkeypatch):
    suite = TestSuite.model_validate(
        {"test_cases": [api_case("AC-1.1"), api_case("AC-1.2", "/missing", 404)]}
    )
    set_id = store.save_suite(
        source_name="r.md",
        raw_text="AC-1.1 AC-1.2",
        target_kind="api",
        base_url=BASE,
        generated_by="import:t",
        drafts=suite.test_cases,
    )
    _run_with(store, set_id, settings, monkeypatch, lambda r: httpx.Response(200))
    root = ET.fromstring(to_junit(store, set_id))
    assert root.attrib["tests"] == "2" and root.attrib["failures"] == "1"
    failure = root.findall("testcase")[1].find("failure")
    assert failure is not None and "expected status_code equals 404" in failure.text


# ---- migrations ---------------------------------------------------------------------------------------
def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE requirement_sets (id TEXT PRIMARY KEY, source_name TEXT, raw_text TEXT, "
        "target_kind TEXT, base_url TEXT, generated_by TEXT, created_at TIMESTAMP);"
        "CREATE TABLE runs (id TEXT PRIMARY KEY, test_case_id TEXT, batch_id TEXT, run_at TEXT, "
        "status TEXT, duration_ms INTEGER, evidence_json TEXT, triage_label TEXT, "
        "triage_explanation TEXT, triage_json TEXT);"
    )
    conn.close()
    s = Store(path)
    cols = {r["name"] for r in s._conn.execute("PRAGMA table_info(runs)")}
    assert "signature" in cols
    Store(path).close()  # idempotent


# ---- helper -----------------------------------------------------------------------------------------
def _run_with(store, set_id, settings, monkeypatch, handler, client=None):
    from uatpilot import runner

    real = runner.execute

    def fake_execute(case, base_url, s, run_id, http_client=None):
        return real(
            case, base_url, s, run_id, make_client(s, transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    events = list(pipeline.run_suite(store, set_id, settings, client=client))
    return events[-1]["batch_id"]
