"""Eval harness: detection rate over injected bugs, and triage accuracy over labelled failures.

Method (written up in evals/README.md):
1. Baseline: run every case in the suite through the proxy with no mutation, twice. Cases that
   pass both times are the *stable* set; the sandbox's own defects and any unstable cases are
   excluded, so they can't be mistaken for detections.
2. Detection: for each mutation, run the stable cases with the mutation active. A mutation is
   detected if at least one stable case fails. If none of the cases covering the broken criterion
   is in the stable set, the mutation is reported as *masked* (it can't be measured), not missed.
3. Triage: for each labelled scenario, run a few stable cases through the full run_case path
   (failure -> immediate re-run -> triage) and compare the label with the known answer.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

from uatpilot.config import Settings
from uatpilot.db import Store
from uatpilot.evals.mutations import CATALOGUE, TRIAGE_SCENARIOS, Mutation, TriageScenario
from uatpilot.evals.proxy import FaultProxy
from uatpilot.models import TestCase
from uatpilot.pipeline import run_case, use_llm
from uatpilot.runner import execute
from uatpilot.triage import TriageAgent

Log = Callable[[str], None]


def _drain(gen):
    """Run a generator that returns a value (run_case), discarding its events."""
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def _covers(case: TestCase, criterion: str) -> bool:
    return any(c.strip() in case.requirement_ref for c in criterion.split(","))


def run_eval(
    store: Store,
    set_id: str,
    settings: Settings,
    *,
    mutations: list[Mutation] | None = None,
    scenarios: list[TriageScenario] | None = None,
    triage_cases: int = 3,
    client: anthropic.Anthropic | None = None,
    log: Log = print,
) -> dict[str, Any]:
    rs = store.requirement_set(set_id)
    if rs is None or rs["target_kind"] != "api":
        raise ValueError("The injected-bug eval needs an API requirement set.")
    mutations = CATALOGUE if mutations is None else mutations
    scenarios = TRIAGE_SCENARIOS if scenarios is None else scenarios
    cases = store.test_cases(set_id)
    proxy = FaultProxy(rs["base_url"], settings)
    base = proxy.start()
    log(f"proxy {base} -> {rs['base_url']}")
    try:
        # 1. baseline, twice
        passes: list[set[str]] = []
        for attempt in (1, 2):
            ok = {c.id for c in cases if execute(c, base, settings, f"base{attempt}")[0]}
            passes.append(ok)
            log(f"baseline {attempt}: {len(ok)}/{len(cases)} pass")
        stable = [c for c in cases if c.id in passes[0] & passes[1]]
        unstable = sorted(passes[0] ^ passes[1])
        failing = [c for c in cases if c.id not in passes[0] | passes[1]]

        # 2. detection
        detections = []
        for m in mutations:
            proxy.set_mode(m.mode)
            covering = [c for c in stable if _covers(c, m.breaks)]
            caught_by = [c.id for c in stable if not execute(c, base, settings, "mut")[0]]
            proxy.set_mode(None)
            status = "detected" if caught_by else ("masked" if not covering else "missed")
            detections.append(
                {
                    "mutation": m.name,
                    "breaks": m.breaks,
                    "description": m.description,
                    "status": status,
                    "caught_by": caught_by,
                    "covering_stable_cases": [c.id for c in covering],
                }
            )
            log(f"  {m.name:<32} {status:<9} {len(caught_by)} case(s)")

        # 3. triage
        agent = (
            TriageAgent(base_url=base, client=client, settings=settings)
            if use_llm(settings)
            else None
        )
        triage_rows = []
        pool = stable[:triage_cases]
        for sc in scenarios:
            s = settings
            if sc.name == "env_timeout":
                s = dataclasses.replace(settings, http_timeout=2.0)
            if agent:
                agent.settings = s
            for case in pool:
                proxy.set_mode(sc.mode())  # fresh mode per case (flaky budget resets)
                result = _drain(run_case(case, base, "eval", s, agent))
                proxy.set_mode(None)
                got = result.triage.label.value if result.triage else "pass"
                triage_rows.append(
                    {
                        "scenario": sc.name,
                        "case": case.id,
                        "expected": sc.expected,
                        "got": got,
                        "correct": got == sc.expected,
                        "status": result.status.value,
                        "engine": result.triage.engine if result.triage else None,
                        "floor_applied": bool(result.triage and result.triage.floor_applied),
                    }
                )
                log(
                    f"  {sc.name:<18} {case.id.split('-')[-1]} expected {sc.expected:<17} got {got}"
                )
    finally:
        proxy.stop()

    measurable = [d for d in detections if d["status"] != "masked"]
    detected = [d for d in measurable if d["status"] == "detected"]
    correct = [t for t in triage_rows if t["correct"]]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "requirement_set": {k: rs[k] for k in ("id", "source_name", "base_url", "generated_by")},
        "triage_engine": "llm" if agent else "rules",
        "model": settings.model_label if agent else None,
        "baseline": {
            "cases": len(cases),
            "stable_pass": len(stable),
            "failing_on_sandbox": [c.id for c in failing],
            "unstable": unstable,
        },
        "detection": {
            "mutations": len(detections),
            "measurable": len(measurable),
            "detected": len(detected),
            "masked": len(detections) - len(measurable),
            "rate": round(len(detected) / len(measurable), 4) if measurable else None,
            "rows": detections,
        },
        "triage": {
            "failures": len(triage_rows),
            "correct": len(correct),
            "accuracy": round(len(correct) / len(triage_rows), 4) if triage_rows else None,
            "never_flipped_to_pass": all(t["status"] == "fail" for t in triage_rows),
            "rows": triage_rows,
        },
    }


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    tag = report["requirement_set"]["generated_by"].split(":")[0]
    jpath = out_dir / f"{tag}-{report['triage_engine']}-{stamp}.json"
    mpath = jpath.with_suffix(".md")
    jpath.write_text(json.dumps(report, indent=2), encoding="utf-8")
    d, t, b = report["detection"], report["triage"], report["baseline"]
    lines = [
        f"# Eval: {report['requirement_set']['source_name']} ({report['generated_at']})",
        "",
        f"- Suite written by: `{report['requirement_set']['generated_by']}`",
        f"- Triage engine: {report['triage_engine']}"
        + (f" (`{report['model']}`)" if report["model"] else ""),
        f"- Baseline: {b['stable_pass']}/{b['cases']} cases pass reliably through the proxy; "
        f"{len(b['failing_on_sandbox'])} fail on the real sandbox (its own defects), "
        f"{len(b['unstable'])} unstable",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Detection rate (injected bugs caught) | **{d['detected']}/{d['measurable']}**"
        + (f" ({d['rate']:.0%})" if d["rate"] is not None else "")
        + f", {d['masked']} masked by existing sandbox defects |",
        f"| Triage accuracy (labelled failures) | **{t['correct']}/{t['failures']}**"
        + (f" ({t['accuracy']:.0%})" if t["accuracy"] is not None else "")
        + " |",
        f"| Failed runs reported as pass | {'0' if t['never_flipped_to_pass'] else 'SOME (bug!)'} |",
        "",
        "## Injected bugs",
        "",
        "| Mutation | Breaks | Result | Caught by |",
        "|---|---|---|---|",
    ]
    for r in d["rows"]:
        lines.append(
            f"| {r['mutation']} | {r['breaks']} | {r['status']} | "
            f"{', '.join(c.split('-')[-1] for c in r['caught_by']) or '-'} |"
        )
    lines += ["", "## Triage", "", "| Scenario | Case | Expected | Got |", "|---|---|---|---|"]
    for r in t["rows"]:
        mark = "" if r["correct"] else " ✗"
        lines.append(
            f"| {r['scenario']} | {r['case'].split('-')[-1]} | {r['expected']} | "
            f"{r['got']}{mark} |"
        )
    lines += [
        "",
        "Same author wrote the requirements, the injected bugs and the system: this is a "
        "smoke test, not an independent benchmark.",
        "",
    ]
    mpath.write_text("\n".join(lines), encoding="utf-8")
    return jpath, mpath
