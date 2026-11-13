"""Strict schemas for everything the agents produce and the runner consumes.

The generation agent's output goes through `TestSuite` before it is stored: a hallucinated field,
an unknown assertion operator or a step with no assertions is rejected with a readable error that
goes back to the model, never written to the database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TargetKind(StrEnum):
    API = "api"
    UI = "ui"


# ---------------------------------------------------------------------------------------------
# API checks (run with httpx)
# ---------------------------------------------------------------------------------------------
class AssertSource(StrEnum):
    STATUS_CODE = "status_code"
    JSON = "json"  # value at `path` in the JSON body, e.g. "booking.firstname" or "[0].bookingid"
    HEADER = "header"  # response header named `path`
    BODY_TEXT = "body_text"  # raw response text
    ELAPSED_MS = "elapsed_ms"


class AssertOp(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    MATCHES = "matches"  # regular expression
    LESS_THAN = "less_than"
    GREATER_THAN = "greater_than"
    TYPE_IS = "type_is"  # string | number | integer | boolean | array | object | null
    LENGTH_AT_LEAST = "length_at_least"


class Assertion(Strict):
    source: AssertSource
    path: str | None = None
    op: AssertOp
    expected: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _check(self) -> Assertion:
        if self.source in (AssertSource.JSON, AssertSource.HEADER) and not self.path:
            raise ValueError(f"source '{self.source}' needs a path")
        if self.op not in (AssertOp.EXISTS, AssertOp.NOT_EXISTS) and self.expected is None:
            if self.op is not AssertOp.EQUALS:  # equals null is a legitimate check
                raise ValueError(f"op '{self.op}' needs an expected value")
        return self


class Header(Strict):
    name: str
    value: str


class ApiStep(Strict):
    name: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
    path: str = Field(description="Path relative to the target base URL; may use {{var}}.")
    headers: list[Header] = Field(default_factory=list)
    query: list[Header] = Field(default_factory=list, description="Query params as name/value.")
    json_body: dict[str, Any] | list[Any] | None = None
    assertions: list[Assertion] = Field(min_length=1)
    capture: list[Header] = Field(
        default_factory=list,
        description="Save a value for later steps: name = variable, value = JSON path.",
    )

    @field_validator("path")
    @classmethod
    def _relative(cls, v: str) -> str:
        if "://" in v:
            raise ValueError("path must be relative to the target base URL, not a full URL")
        return v if v.startswith("/") else "/" + v


class ApiSpec(Strict):
    kind: Literal["api"] = "api"
    steps: list[ApiStep] = Field(min_length=1)


# ---------------------------------------------------------------------------------------------
# UI checks (run with Playwright)
# ---------------------------------------------------------------------------------------------
class UiAction(StrEnum):
    GOTO = "goto"  # value = path relative to base URL
    FILL = "fill"
    CLICK = "click"
    SELECT = "select"  # select an <option> by value
    EXPECT_VISIBLE = "expect_visible"
    EXPECT_HIDDEN = "expect_hidden"
    EXPECT_TEXT = "expect_text"  # selector's text contains value
    EXPECT_URL = "expect_url"  # current URL contains value
    EXPECT_COUNT = "expect_count"  # number of elements matching selector == value
    EXPECT_ATTRIBUTE = "expect_attribute"  # value = "attr=expected-substring"


_NEEDS_SELECTOR = {
    UiAction.FILL,
    UiAction.CLICK,
    UiAction.SELECT,
    UiAction.EXPECT_VISIBLE,
    UiAction.EXPECT_HIDDEN,
    UiAction.EXPECT_TEXT,
    UiAction.EXPECT_COUNT,
    UiAction.EXPECT_ATTRIBUTE,
}
_NEEDS_VALUE = {
    UiAction.GOTO,
    UiAction.FILL,
    UiAction.SELECT,
    UiAction.EXPECT_TEXT,
    UiAction.EXPECT_URL,
    UiAction.EXPECT_COUNT,
    UiAction.EXPECT_ATTRIBUTE,
}


class UiStep(Strict):
    action: UiAction
    selector: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def _check(self) -> UiStep:
        if self.action in _NEEDS_SELECTOR and not self.selector:
            raise ValueError(f"action '{self.action}' needs a selector")
        if self.action in _NEEDS_VALUE and self.value is None:
            raise ValueError(f"action '{self.action}' needs a value")
        if self.action is UiAction.EXPECT_COUNT and not str(self.value).isdigit():
            raise ValueError("expect_count value must be a whole number")
        return self

    @property
    def is_assertion(self) -> bool:
        return self.action.value.startswith("expect_")


class UiSpec(Strict):
    kind: Literal["ui"] = "ui"
    steps: list[UiStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _has_assertion(self) -> UiSpec:
        if not any(s.is_assertion for s in self.steps):
            raise ValueError("a UI check needs at least one expect_* step, or it can never fail")
        return self


ExecutableSpec = Annotated[ApiSpec | UiSpec, Field(discriminator="kind")]


# ---------------------------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------------------------
class TestCaseDraft(Strict):
    """What the generation agent writes. Ids and bookkeeping are added by the code."""

    __test__ = False  # not a pytest class

    title: str = Field(min_length=5, max_length=160)
    requirement_ref: str = Field(
        min_length=1, description="The requirement/acceptance-criterion id this case covers."
    )
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1, description="Human-readable steps for the UAT doc.")
    expected_result: str = Field(min_length=3)
    priority: Priority
    executable: ExecutableSpec

    @property
    def target_kind(self) -> TargetKind:
        return TargetKind(self.executable.kind)


class TestSuite(Strict):
    __test__ = False
    test_cases: list[TestCaseDraft] = Field(min_length=1, max_length=40)


class TestCase(TestCaseDraft):
    __test__ = False
    id: str
    requirement_set_id: str


# ---------------------------------------------------------------------------------------------
# Runs and triage
# ---------------------------------------------------------------------------------------------
class RunStatus(StrEnum):
    PASS = "pass"  # noqa: S105 (a test outcome, not a password)
    FAIL = "fail"


class TriageLabel(StrEnum):
    REAL_REGRESSION = "real_regression"
    FLAKY = "flaky"
    ENVIRONMENT_ISSUE = "environment_issue"


class TriageDecision(Strict):
    """What the triage agent submits."""

    label: TriageLabel
    explanation: str = Field(min_length=10, max_length=400)
    confidence: Literal["low", "medium", "high"]


class Triage(BaseModel):
    label: TriageLabel
    explanation: str
    confidence: str
    engine: Literal["claude", "rules"]
    floor_applied: dict[str, Any] | None = None


class RunResult(BaseModel):
    id: str
    test_case_id: str
    batch_id: str
    run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: RunStatus
    duration_ms: int
    evidence: dict[str, Any]
    triage: Triage | None = None


def suite_json_schema() -> dict[str, Any]:
    """The TestSuite JSON schema with $refs inlined, for use as a tool input_schema."""
    return inline_refs(TestSuite.model_json_schema())


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].split("/")[-1]]
                merged = {**walk(target), **{k: v for k, v in node.items() if k != "$ref"}}
                return merged
            # Drop pydantic's "title" annotations (strings) but keep a *property* named title.
            return {
                k: walk(v)
                for k, v in node.items()
                if k not in ("$defs", "discriminator")  # OpenAPI-only keyword, not JSON Schema
                and not (k == "title" and isinstance(v, str))
            }
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
