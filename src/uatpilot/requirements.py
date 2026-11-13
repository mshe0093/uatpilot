"""Reading the inputs: acceptance-criterion ids from a requirements doc (for the coverage gate
and the traceability matrix) and a condensed OpenAPI spec (for the generation agent)."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from uatpilot.models import TestSuite

# AC-2.1, NFR-1, REQ-3, FR-12, BR-4.2.1. User-story ids (US-1) group criteria; they aren't
# criteria themselves, so they are not required to have their own case.
CRITERION = re.compile(r"\b(?:AC|NFR|REQ|FR|BR)-\d+(?:\.\d+)*\b")


def extract_criteria(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in CRITERION.finditer(text):
        seen.setdefault(m.group(0))
    return list(seen)


def coverage(criteria: list[str], suite: TestSuite) -> dict[str, Any]:
    """Which criteria have a case, which were declared not coverable, which are silently missing,
    and which references point at ids the document doesn't contain."""
    covered: dict[str, list[int]] = {}
    for i, case in enumerate(suite.test_cases, start=1):
        for ref in CRITERION.findall(case.requirement_ref):
            covered.setdefault(ref, []).append(i)
    declared = {n.requirement_ref.strip(): n.reason for n in suite.not_covered}
    known = set(criteria)
    return {
        "criteria": criteria,
        "covered": {c: covered[c] for c in criteria if c in covered},
        "not_covered": {c: declared[c] for c in criteria if c in declared and c not in covered},
        "missing": [c for c in criteria if c not in covered and c not in declared],
        "unknown_refs": sorted((set(covered) | set(declared)) - known) if known else [],
    }


def coverage_problems(report: dict[str, Any]) -> str | None:
    lines = []
    if report["missing"]:
        lines.append(
            "These acceptance criteria have no test case and are not listed in not_covered: "
            + ", ".join(report["missing"])
            + ". Add cases for them, or list them in not_covered with a reason."
        )
    if report["unknown_refs"]:
        lines.append(
            "These requirement_ref ids do not appear in the requirements document: "
            + ", ".join(report["unknown_refs"])
            + ". Use the document's own ids."
        )
    return "\n".join(lines) or None


# ---- OpenAPI ---------------------------------------------------------------------------------
def parse_spec(text: str) -> dict[str, Any]:
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        spec = yaml.safe_load(text)
    if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
        raise ValueError("Not an OpenAPI/Swagger document (no 'openapi' or 'swagger' key).")
    return spec


def _resolve(node: Any, root: dict[str, Any], depth: int = 0) -> Any:
    if depth > 12:
        return {"description": "(nested too deep; truncated)"}
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = root
            for part in ref[2:].split("/"):
                target = (
                    target.get(part.replace("~1", "/").replace("~0", "~"), {})
                    if isinstance(target, dict)
                    else {}
                )
            return _resolve(target, root, depth + 1)
        return {k: _resolve(v, root, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, root, depth + 1) for v in node]
    return node


_METHODS = ("get", "post", "put", "patch", "delete", "head")


def condense_openapi(spec: dict[str, Any], limit: int = 40_000) -> str:
    """One compact JSON block per operation: parameters, request schema, response schemas.
    $refs are resolved so the agent can copy a schema straight into a matches_schema assertion."""
    ops = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            entry: dict[str, Any] = {"op": f"{method.upper()} {path}"}
            if summary := op.get("summary") or op.get("description"):
                entry["summary"] = str(summary)[:200]
            params = [*(item.get("parameters") or []), *(op.get("parameters") or [])]
            if params:
                entry["parameters"] = [
                    {k: p.get(k) for k in ("name", "in", "required") if k in p}
                    for p in _resolve(params, spec)
                ]
            body = _resolve(op.get("requestBody") or {}, spec)
            if schema := _json_schema(body):
                entry["request_schema"] = schema
            responses = {}
            for code, resp in (op.get("responses") or {}).items():
                resp = _resolve(resp, spec)
                responses[str(code)] = _json_schema(resp) or (resp.get("description") or "")[:120]
            if responses:
                entry["responses"] = responses
            ops.append(entry)
    text = json.dumps(ops, separators=(",", ":"))
    if len(text) > limit:
        text = text[:limit] + "...(spec truncated)"
    return text


def _json_schema(obj: dict[str, Any]) -> dict[str, Any] | None:
    content = obj.get("content") or {}
    for media, value in content.items():
        if "json" in media and isinstance(value, dict) and value.get("schema"):
            return value["schema"]
    if "schema" in obj and isinstance(obj["schema"], dict):  # Swagger 2
        return obj["schema"]
    return None
