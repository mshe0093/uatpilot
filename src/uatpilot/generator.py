"""The test-case generation agent: requirements doc + live target -> validated TestSuite."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from uatpilot.config import Settings, get_settings
from uatpilot.llm import Event, Tool, ToolLoop, ToolOutcome
from uatpilot.models import TargetKind, TestSuite, UiAction, UiStep, suite_json_schema
from uatpilot.prompts import GENERATION_NUDGE, GENERATION_SYSTEM
from uatpilot.requirements import coverage, coverage_problems, extract_criteria
from uatpilot.runner.ui import inspect_page
from uatpilot.target import TargetNotAllowed, probe


def format_validation_error(exc: ValidationError, limit: int = 25) -> str:
    lines = []
    for err in exc.errors()[:limit]:
        where = ".".join(str(p) for p in err["loc"])
        lines.append(f"- {where}: {err['msg']}")
    more = len(exc.errors()) - limit
    if more > 0:
        lines.append(f"- ...and {more} more")
    return "Suite rejected by the schema. Fix these and resubmit the whole suite:\n" + "\n".join(
        lines
    )


class Generator:
    def __init__(
        self,
        *,
        base_url: str,
        target_kind: TargetKind,
        client: Any = None,  # injected SDK client (tests); built from settings otherwise
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = base_url
        self.target_kind = target_kind
        self.probes = 0
        self.criteria: list[str] = []
        self.coverage: dict[str, Any] | None = None
        self.loop = ToolLoop(
            client=client,
            settings=self.settings,
            system=GENERATION_SYSTEM,
            tools=self._tools(),
            submit_tool="submit_test_cases",
            nudge=GENERATION_NUDGE,
        )

    # ---- tools ------------------------------------------------------------------------------
    def _budget(self) -> ToolOutcome | None:
        self.probes += 1
        if self.probes > self.settings.max_probes:
            return ToolOutcome(
                f"Probe budget ({self.settings.max_probes}) used up. Write the suite from what "
                "you have already seen.",
                is_error=True,
            )
        return None

    def _http_probe(self, args: dict[str, Any]) -> ToolOutcome:
        if over := self._budget():
            return over
        try:
            result = probe(self.base_url, str(args.get("path", "/")), self.settings)
        except TargetNotAllowed as exc:
            return ToolOutcome(str(exc), is_error=True)
        except Exception as exc:
            return ToolOutcome(f"Request failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolOutcome(json.dumps(result))

    def _inspect_page(self, args: dict[str, Any]) -> ToolOutcome:
        if over := self._budget():
            return over
        try:
            steps = [UiStep.model_validate(s) for s in args.get("setup_steps") or []]
            result = inspect_page(self.base_url, steps, self.settings)
        except ValidationError as exc:
            return ToolOutcome(format_validation_error(exc), is_error=True)
        except Exception as exc:
            return ToolOutcome(f"Inspection failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolOutcome(json.dumps(result))

    def _submit(self, args: dict[str, Any]) -> ToolOutcome:
        try:
            suite = TestSuite.model_validate(args)
        except ValidationError as exc:
            return ToolOutcome(format_validation_error(exc), is_error=True)
        wrong = [c.title for c in suite.test_cases if c.target_kind is not self.target_kind]
        if wrong:
            return ToolOutcome(
                f"This target is a {self.target_kind.value} target; these cases use the other "
                f"kind: {wrong[:5]}. Resubmit with executable.kind = '{self.target_kind.value}'.",
                is_error=True,
            )
        if vacuous := self._vacuous_url_checks(suite):
            return ToolOutcome(
                "These expect_url steps would pass on every page of the site, because every URL "
                f"there contains the value: {vacuous[:5]}. Use a value that only the expected "
                "page's URL contains (a path such as '/inventory.html'), or check an element that "
                "only that page has.",
                is_error=True,
            )
        # Coverage gate (qodo-cover gates generated tests on code coverage; for UAT the unit is
        # the acceptance criterion): every criterion is covered or explicitly declared not.
        report = coverage(self.criteria, suite)
        if problems := coverage_problems(report):
            return ToolOutcome(problems, is_error=True)
        self.coverage = report
        return ToolOutcome(f"Accepted {len(suite.test_cases)} test cases.", accepted=suite)

    def _vacuous_url_checks(self, suite: TestSuite) -> list[str]:
        """expect_url is a 'contains' check, so a value like the site root matches every page.
        Seen in review: a logout case checked for 'https://www.saucedemo.com/', which the
        products page URL also contains, so the case could never fail."""
        root = self.base_url.rstrip("/") + "/"
        found = []
        for case in suite.test_cases:
            ui = case.executable if case.target_kind is TargetKind.UI else None
            for step in getattr(ui, "steps", []):
                value = (step.value or "").strip()
                if step.action is UiAction.EXPECT_URL and (not value or value in root):
                    found.append(f"{case.title}: {value!r}")
        return found

    def _tools(self) -> list[Tool]:
        tools = [
            Tool(
                name="submit_test_cases",
                description=(
                    "Submit the complete UAT test suite. It is validated against a strict schema; "
                    "on rejection you get the exact errors to fix."
                ),
                input_schema=suite_json_schema(),
                handler=self._submit,
            )
        ]
        if self.target_kind is TargetKind.API:
            tools.insert(
                0,
                Tool(
                    name="http_probe",
                    description=(
                        "Send one read-only GET request to the target API and see the real "
                        "status code and (truncated) JSON body. Use it to learn response shapes "
                        f"before writing assertions. At most {self.settings.max_probes} calls."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path relative to the base URL, e.g. /booking/1",
                            }
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    handler=self._http_probe,
                ),
            )
        else:
            step_schema = suite_json_schema()  # reuse the UiStep schema from the suite schema
            ui_step = _find_ui_step_schema(step_schema)
            tools.insert(
                0,
                Tool(
                    name="inspect_page",
                    description=(
                        "Open the target in a real browser, optionally run goto/fill/click setup "
                        "steps first (e.g. to log in with credentials from the requirements), and "
                        "list the visible interactive elements with their id, name, data-test and "
                        f"text. At most {self.settings.max_probes} calls."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {"setup_steps": {"type": "array", "items": ui_step}},
                        "additionalProperties": False,
                    },
                    handler=self._inspect_page,
                ),
            )
        return tools

    # ---- run --------------------------------------------------------------------------------
    def generate(
        self, requirements: str, source_name: str, openapi: str | None = None
    ) -> Iterator[Event]:
        """Yields trace events. When it finishes, `self.suite` holds the TestSuite and
        `self.coverage` the traceability report. `openapi` is a condensed spec
        (requirements.condense_openapi)."""
        self.criteria = extract_criteria(requirements)
        prompt = (
            f"Target kind: {self.target_kind.value}\n"
            f"Target base URL: {self.base_url}\n"
            f"Acceptance criteria found in the document: {', '.join(self.criteria) or '(no ids)'}\n"
            f"Requirements document ({source_name}):\n"
            f"<requirements>\n{requirements}\n</requirements>"
        )
        if openapi:
            prompt += (
                "\nOpenAPI spec for the target (condensed; $refs resolved):\n"
                f"<openapi>\n{openapi}\n</openapi>"
            )
        yield {
            "type": "start",
            "source_name": source_name,
            "target": self.base_url,
            "criteria": len(self.criteria),
            "openapi": bool(openapi),
        }
        yield from self.loop.run(prompt)

    @property
    def suite(self) -> TestSuite | None:
        return self.loop.result


def _find_ui_step_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pull the inlined UiStep schema out of the TestSuite schema."""
    variants = schema["properties"]["test_cases"]["items"]["properties"]["executable"]
    for v in variants.get("oneOf", variants.get("anyOf", [])):
        if v.get("properties", {}).get("kind", {}).get("const") == "ui":
            return v["properties"]["steps"]["items"]
    raise RuntimeError("UiStep schema not found")
