"""Orchestration shared by the CLI and the web API: run a suite, triage failures, store results."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anthropic

from uatpilot.config import Settings, has_claude_credentials
from uatpilot.db import Store, new_id
from uatpilot.llm import Event
from uatpilot.models import RunResult, RunStatus, TestCase
from uatpilot.runner import execute
from uatpilot.triage import TriageAgent, first_failure, triage_or_fallback


def use_claude(settings: Settings) -> bool:
    if settings.engine == "rules":
        return False
    if settings.engine == "claude":
        return True
    return has_claude_credentials()


def run_suite(
    store: Store,
    set_id: str,
    settings: Settings,
    *,
    client: anthropic.Anthropic | None = None,
    case_ids: list[str] | None = None,
    base_url: str | None = None,
) -> Iterator[Event]:
    rs = store.requirement_set(set_id)
    if rs is None:
        yield {"type": "error", "message": f"No requirement set {set_id}"}
        return
    base = base_url or rs["base_url"]
    cases = [c for c in store.test_cases(set_id) if not case_ids or c.id in case_ids]
    agent = (
        TriageAgent(base_url=base, client=client, settings=settings)
        if use_claude(settings)
        else None
    )
    batch_id = store.start_batch(set_id, base)
    yield {
        "type": "batch_start",
        "batch_id": batch_id,
        "total": len(cases),
        "base_url": base,
        "triage_engine": "claude" if agent else "rules",
    }
    counts = {"pass": 0, "fail": 0}
    for case in cases:
        yield {"type": "case_start", "test_case_id": case.id, "title": case.title}
        run = yield from run_case(case, base, batch_id, settings, agent)
        store.save_run(run)
        counts[run.status.value] += 1
        yield {"type": "case_result", "run": run.model_dump(mode="json"), "title": case.title}
    store.finish_batch(batch_id)
    yield {"type": "batch_done", "batch_id": batch_id, **counts}


def run_case(
    case: TestCase,
    base_url: str,
    batch_id: str,
    settings: Settings,
    agent: TriageAgent | None,
) -> Iterator[Event]:
    """Generator that yields trace events and *returns* the RunResult."""
    run_id = new_id("run")
    passed, duration, evidence = execute(case, base_url, settings, run_id)
    status = RunStatus.PASS if passed else RunStatus.FAIL
    triage = None
    if not passed:
        # Immediate re-run: the only honest evidence for "flaky".
        re_passed, _, re_evidence = execute(case, base_url, settings, run_id + "_rerun")
        rerun: dict[str, Any] = {"passed": re_passed}
        if not re_passed:
            rerun["first_failure"] = first_failure(re_evidence)
        evidence["rerun"] = rerun
        yield {"type": "rerun", "test_case_id": case.id, "passed": re_passed}
        for ev in triage_or_fallback(agent, case, evidence, rerun):
            if ev["type"] == "triage":
                triage = ev["triage"]
            else:
                yield {**ev, "test_case_id": case.id, "phase": "triage"}
    # The status above is final: triage only attaches a label (see triage.apply_triage_floor).
    return RunResult(
        id=run_id,
        test_case_id=case.id,
        batch_id=batch_id,
        status=status,
        duration_ms=duration,
        evidence=evidence,
        triage=triage,
    )
