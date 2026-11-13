"""Run one test case against its target and time it."""

from __future__ import annotations

import time
from typing import Any

import httpx

from uatpilot.config import Settings
from uatpilot.models import ApiSpec, TestCase
from uatpilot.runner.api import run_api_spec
from uatpilot.runner.ui import run_ui_spec
from uatpilot.target import TargetNotAllowed


def execute(
    case: TestCase,
    base_url: str,
    settings: Settings,
    run_id: str,
    http_client: httpx.Client | None = None,
) -> tuple[bool, int, dict[str, Any]]:
    """Returns (passed, duration_ms, evidence)."""
    started = time.perf_counter()
    try:
        if isinstance(case.executable, ApiSpec):
            passed, evidence = run_api_spec(case.executable, base_url, settings, http_client)
        else:
            passed, evidence = run_ui_spec(case.executable, base_url, settings, run_id)
    except TargetNotAllowed as exc:
        passed, evidence = (
            False,
            {"steps": [], "error": {"kind": "not_allowed", "detail": str(exc)}},
        )
    except Exception as exc:  # e.g. Playwright missing; recorded as evidence, never swallowed
        passed, evidence = (
            False,
            {
                "steps": [],
                "error": {"kind": "runner", "detail": f"{type(exc).__name__}: {exc}"},
            },
        )
    return passed, round((time.perf_counter() - started) * 1000), evidence
