"""The UAT sign-off document: the artifact these jobs ask a human to produce by hand."""

from __future__ import annotations

import csv
import io
from typing import Any

from uatpilot.db import Store
from uatpilot.requirements import CRITERION, extract_criteria
from uatpilot.triage import first_failure

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
        "## Requirements traceability",
        "",
        "| Criterion | Test cases | Result |",
        "|---|---|---|",
    ]
    for row in traceability(store, set_id, runs):
        ids = ", ".join(row["cases"]) or "-"
        out.append(f"| {row['criterion']} | {ids} | {_cell(row['result'])} |")
    out += [
        "",
        "## Summary",
        "",
        "| ID | Requirement | Title | Priority | Result | Since last run | Triage |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in cases:
        r = runs.get(c.id)
        status = _ICON[r["status"] if r else None]
        triage = (r or {}).get("triage_label") or ""
        change = _CHANGE.get((r or {}).get("change", ""), "")
        out.append(
            f"| {c.id.split('-')[-1]} | {c.requirement_ref} | {_cell(c.title)} | "
            f"{c.priority.value} | {status} | {change} | {triage.replace('_', ' ')} |"
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


_CHANGE = {
    "new_failure": "NEW FAILURE",
    "fixed": "fixed",
    "still_failing": "still failing",
    "still_passing": "",
    "first_run": "",
}


def traceability(
    store: Store, set_id: str, runs: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """One row per acceptance criterion in the document: which cases cover it and how they did.
    A criterion with no case shows the agent's declared reason, or MISSING if there is none."""
    rs = store.requirement_set(set_id)
    if rs is None:
        raise KeyError(set_id)
    if runs is None:
        runs = _latest(store, set_id)[1]
    cases = store.test_cases(set_id)
    declared = (rs.get("coverage") or {}).get("not_covered", {})
    rows = []
    for crit in extract_criteria(rs["raw_text"]):
        covering = [c for c in cases if crit in CRITERION.findall(c.requirement_ref)]
        statuses = [runs[c.id]["status"] for c in covering if c.id in runs]
        if not covering:
            result = f"NOT COVERED: {declared[crit]}" if crit in declared else "MISSING"
        elif len(statuses) < len(covering):
            result = "NOT RUN"
        elif all(s == "pass" for s in statuses):
            result = "PASS"
        else:
            result = f"FAIL ({statuses.count('fail')}/{len(statuses)})"
        rows.append(
            {"criterion": crit, "cases": [c.id.split("-")[-1] for c in covering], "result": result}
        )
    return rows


def to_junit(store: Store, set_id: str) -> str:
    """JUnit XML for the latest run, so CI systems and other report tools (Allure, GitHub test
    reporters) can read UATPilot results. Triage goes into the failure message."""
    from xml.etree import ElementTree as ET

    rs = store.requirement_set(set_id)
    if rs is None:
        raise KeyError(set_id)
    batch, runs = _latest(store, set_id)
    cases = store.test_cases(set_id)
    failures = sum(1 for r in runs.values() if r["status"] == "fail")
    total_s = sum(r["duration_ms"] for r in runs.values()) / 1000
    suite = ET.Element(
        "testsuite",
        name=rs["source_name"],
        tests=str(len(cases)),
        failures=str(failures),
        skipped=str(len(cases) - len(runs)),
        time=f"{total_s:.3f}",
        timestamp=(batch or {}).get("started_at") or "",
    )
    for c in cases:
        r = runs.get(c.id)
        tc = ET.SubElement(
            suite,
            "testcase",
            classname=f"{rs['source_name']}.{c.requirement_ref}",
            name=f"{c.id.split('-')[-1]} {c.title}",
            time=f"{(r or {}).get('duration_ms', 0) / 1000:.3f}",
        )
        if r is None:
            ET.SubElement(tc, "skipped", message="not run")
        elif r["status"] == "fail":
            t = r.get("triage") or {}
            label = (t.get("label") or "untriaged").replace("_", " ")
            fail = ET.SubElement(
                tc, "failure", message=f"{label}: {t.get('explanation', '')}"[:500], type=label
            )
            fail.text = first_failure(r["evidence"])
    return ET.tostring(suite, encoding="unicode", xml_declaration=True)


def _cell(value: Any) -> Any:
    """Neutralise spreadsheet formulas (OWASP CSV injection): titles and explanations come from a
    model or a user, and a cell starting with = + - @ (or a tab/CR) runs as a formula in Excel."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


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
            "since_last_run",
            "triage_label",
            "triage_explanation",
            "run_at",
        ]
    )
    for c in cases:
        r = runs.get(c.id) or {}
        w.writerow(
            [
                _cell(v)
                for v in [
                    c.id,
                    c.requirement_ref,
                    c.title,
                    c.priority.value,
                    "; ".join(c.preconditions),
                    " / ".join(f"{i}. {s}" for i, s in enumerate(c.steps, 1)),
                    c.expected_result,
                    r.get("status", "not_run"),
                    r.get("change", ""),
                    r.get("triage_label") or "",
                    r.get("triage_explanation") or "",
                    r.get("run_at") or "",
                ]
            ]
        )
    return buf.getvalue()
