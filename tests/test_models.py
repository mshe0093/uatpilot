import json

import pytest
from pydantic import ValidationError

from uatpilot.models import ApiStep, Assertion, TestSuite, UiSpec, UiStep, suite_json_schema


def api_case(**over):
    case = {
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
                    "assertions": [{"source": "status_code", "op": "equals", "expected": 201}],
                }
            ],
        },
    }
    case.update(over)
    return case


def test_valid_suite_parses():
    suite = TestSuite.model_validate({"test_cases": [api_case()]})
    assert suite.test_cases[0].target_kind.value == "api"


def test_hallucinated_field_is_rejected():
    with pytest.raises(ValidationError, match="extra"):
        TestSuite.model_validate({"test_cases": [api_case(severity="P1")]})


def test_step_without_assertions_is_rejected():
    bad = api_case()
    bad["executable"]["steps"][0]["assertions"] = []
    with pytest.raises(ValidationError):
        TestSuite.model_validate({"test_cases": [bad]})


def test_unknown_operator_rejected():
    with pytest.raises(ValidationError):
        Assertion.model_validate({"source": "status_code", "op": "roughly", "expected": 200})


def test_json_assertion_needs_path():
    with pytest.raises(ValidationError, match="needs a path"):
        Assertion(source="json", op="exists")


def test_absolute_url_path_rejected():
    with pytest.raises(ValidationError, match="relative"):
        ApiStep(
            name="x",
            method="GET",
            path="https://evil.example/steal",
            assertions=[Assertion(source="status_code", op="equals", expected=200)],
        )


def test_path_gets_leading_slash():
    step = ApiStep(
        name="x",
        method="GET",
        path="booking",
        assertions=[Assertion(source="status_code", op="equals", expected=200)],
    )
    assert step.path == "/booking"


def test_ui_spec_needs_an_expect_step():
    with pytest.raises(ValidationError, match="expect_"):
        UiSpec(steps=[UiStep(action="goto", value="/")])


def test_ui_step_needs_selector():
    with pytest.raises(ValidationError, match="selector"):
        UiStep(action="click")


def test_tool_schema_is_self_contained_and_keeps_title_property():
    schema = suite_json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and "discriminator" not in text
    props = schema["properties"]["test_cases"]["items"]["properties"]
    assert "title" in props and "executable" in props
