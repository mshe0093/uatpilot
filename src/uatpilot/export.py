"""The UAT sign-off document: the artifact these jobs ask a human to produce by hand."""

from __future__ import annotations

import csv
import io
from typing import Any

from uatpilot.db import Store

_ICON = {"pass": "PASS", "fail": "FAIL", None: "NOT RUN"}


def _latest(store: Store, set_id: str) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    batches = store.batches(set_id, limit=1)
    if not batches:
        return None, {}
    batch = batches[0]
    return batch, {r["test_case_id"]: r for r in store.batch_runs(batch["id"])}


def to_markdown(store: Store, set_id: str) -> str:
    rs = store.requirement_set(set_id)
    if rs is None:
        raise KeyError(set_id)
    cases = store.test_cases(set_id)
    batch, runs = _latest(store, set_id)
    passed = sum(1 for r in runs.values() if r["status"] == "pass")
    out = [
        f"# UAT Sign-off: {rs['source_name']}",
        "",
        "| | |",
        "|---|---|",
        f"| Requirement set | `{rs['id']}` |",
        f"| Target | {rs['base_url']} ({rs['target_kind'].upper()}) |",
        f"| Test cases written by | {rs['generated_by']} |",
        f"| Last run | {batch['started_at'] if batch else 'not run yet'} |",
        f"| Result | {passed}/{len(cases)} passed |" if batch else "| Result | - |",
        "",
        "## Summary",
        "",
        "| ID | Requirement | Title | Priority | Result | Triage |",
        "|---|---|---|---|---|---|",
    ]
    for c in cases:
        r = runs.get(c.id)
        status = _ICON[r["status"] if r else None]
        triage = (r or {}).get("triage_label") or ""
        out.append(
            f"| {c.id.split('-')[-1]} | {c.requirement_ref} | {_cell(c.title)} | "
            f"{c.priority.value} | {status} | {triage.replace('_', ' ')} |"
        )
    out += ["", "## Test cases", ""]
    for c in cases:
        r = runs.get(c.id)
        out += [f"### {c.id.split('-')[-1]} - {c.title}", ""]
        out.append(f"**Covers:** {c.requirement_ref} &nbsp; **Priority:** {c.priority.value}")
        out.append("")
        if c.preconditions:
            out.append("**Preconditions**")
            out += [f"- {p}" for p in c.preconditions]
            out.append("")
        out.append("**Steps**")
        out += [f"{i}. {s}" for i, s in enumerate(c.steps, start=1)]
        out += ["", f"**Expected result:** {c.expected_result}", ""]
        out.append(f"**Actual result:** {_ICON[r['status'] if r else None]}")
        if r and r.get("triage_explanation"):
            out.append(f"  ({r['triage_label'].replace('_', ' ')}: {r['triage_explanation']})")
        out += ["", "**Tester sign-off:** ______________ &nbsp; **Date:** __________", ""]
    out += [
        "---",
        "",
        "Results are from real executions against the target. A FAIL is never changed to PASS by "
        "the triage step; the triage label is an explanation for the reviewer, not an override.",
        "",
    ]
    return "\n".join(out)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_csv(store: Store, set_id: str) -> str:
    cases = store.test_cases(set_id)
    _, runs = _latest(store, set_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "requirement_ref",
            "title",
            "priority",
            "preconditions",
            "steps",
            "expected_result",
            "status",
            "triage_label",
            "triage_explanation",
            "run_at",
        ]
    )
    for c in cases:
        r = runs.get(c.id) or {}
        w.writerow(
            [
                c.id,
                c.requirement_ref,
                c.title,
                c.priority.value,
                "; ".join(c.preconditions),
                " / ".join(f"{i}. {s}" for i, s in enumerate(c.steps, 1)),
                c.expected_result,
                r.get("status", "not_run"),
                r.get("triage_label") or "",
                r.get("triage_explanation") or "",
                r.get("run_at") or "",
            ]
        )
    return buf.getvalue()
