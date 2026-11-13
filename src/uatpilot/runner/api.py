"""Execute an ApiSpec for real: each step is an actual HTTP call whose actual response is compared
against the assertions. Values captured from one step ({{booking_id}}) feed later steps."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from uatpilot.config import Settings
from uatpilot.models import ApiSpec, Assertion, AssertOp, AssertSource
from uatpilot.target import check_target, join, make_client, summarize_body

_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_MISSING = object()


class StepError(Exception):
    """Transport-level failure (timeout, connection refused...). Not an assertion failure."""


# ---- JSON paths ------------------------------------------------------------------------------
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve(data: Any, path: str) -> Any:
    """'booking.firstname', '[0].bookingid', 'items[2].name', 'length()' on a list/str."""
    node = data
    for part in path.split("."):
        if part == "length()":
            if isinstance(node, list | str | dict):
                node = len(node)
                continue
            return _MISSING
        for key, idx in _TOKEN.findall(part):
            if key:
                if not isinstance(node, dict) or key not in node:
                    return _MISSING
                node = node[key]
            else:
                i = int(idx)
                if not isinstance(node, list) or i >= len(node):
                    return _MISSING
                node = node[i]
    return node


def substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _VAR.fullmatch(value.strip())
        if whole and whole.group(1) in variables:  # keep the original type (int ids stay int)
            return variables[whole.group(1)]
        return _VAR.sub(lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    return value


# ---- assertions -------------------------------------------------------------------------------
_TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def _actual(a: Assertion, resp: httpx.Response, elapsed_ms: int) -> Any:
    if a.source is AssertSource.STATUS_CODE:
        return resp.status_code
    if a.source is AssertSource.ELAPSED_MS:
        return elapsed_ms
    if a.source is AssertSource.BODY_TEXT:
        return resp.text
    if a.source is AssertSource.HEADER:
        return resp.headers.get(a.path or "", _MISSING)
    try:
        body = resp.json()
    except ValueError:
        return _MISSING
    return resolve(body, a.path or "")


def _loose_equal(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    # 200 vs "200", 1 vs "1": the model writes expected values as JSON, the API returns ints.
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).lower() == str(expected).lower()
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        return str(actual) == str(expected)


def check(a: Assertion, resp: httpx.Response, elapsed_ms: int) -> dict[str, Any]:
    actual = _actual(a, resp, elapsed_ms)
    exp = a.expected
    missing = actual is _MISSING
    try:
        match a.op:
            case AssertOp.EXISTS:
                ok = not missing
            case AssertOp.NOT_EXISTS:
                ok = missing
            case _ if missing:
                ok = False
            case AssertOp.EQUALS:
                ok = _loose_equal(actual, exp)
            case AssertOp.NOT_EQUALS:
                ok = not _loose_equal(actual, exp)
            case AssertOp.CONTAINS:
                ok = (
                    str(exp) in actual
                    if isinstance(actual, str)
                    else exp in actual
                    if isinstance(actual, list)
                    else str(exp) in str(actual)
                )
            case AssertOp.MATCHES:
                ok = re.search(str(exp), str(actual)) is not None
            case AssertOp.LESS_THAN:
                ok = float(actual) < float(exp)  # type: ignore[arg-type]
            case AssertOp.GREATER_THAN:
                ok = float(actual) > float(exp)  # type: ignore[arg-type]
            case AssertOp.TYPE_IS:
                ok = _TYPES.get(str(exp), lambda _v: False)(actual)
            case AssertOp.LENGTH_AT_LEAST:
                ok = len(actual) >= int(exp)  # type: ignore[arg-type]
            case _:
                ok = False
    except (TypeError, ValueError, re.error) as exc:
        return {
            **a.model_dump(mode="json"),
            "passed": False,
            "actual": repr(actual),
            "error": str(exc),
        }
    shown = "<missing>" if missing else actual
    if isinstance(shown, str) and len(shown) > 300:
        shown = shown[:300] + "...(truncated)"
    return {**a.model_dump(mode="json"), "passed": ok, "actual": shown}


# ---- execution --------------------------------------------------------------------------------
def run_api_spec(
    spec: ApiSpec,
    base_url: str,
    settings: Settings,
    client: httpx.Client | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Returns (passed, evidence). Stops at the first failing step: later steps usually depend on
    it, and running them would just add noise to the triage evidence."""
    own = client is None
    client = client or make_client(settings)
    variables: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    try:
        for step in spec.steps:
            url = join(base_url, substitute(step.path, variables))
            check_target(url, settings)
            headers = {h.name: substitute(h.value, variables) for h in step.headers}
            params = {q.name: substitute(q.value, variables) for q in step.query}
            body = substitute(step.json_body, variables) if step.json_body is not None else None
            record: dict[str, Any] = {
                "name": step.name,
                "request": {
                    "method": step.method,
                    "url": url,
                    "headers": headers,
                    "params": params,
                    "json": body,
                },
            }
            steps.append(record)
            started = time.perf_counter()
            try:
                resp = client.request(
                    step.method, url, headers=headers, params=params or None, json=body
                )
            except httpx.TimeoutException as exc:
                record["error"] = {"kind": "timeout", "detail": str(exc) or "request timed out"}
                return False, {"steps": steps, "failed_step": step.name}
            except httpx.TransportError as exc:
                record["error"] = {"kind": "connection", "detail": str(exc)}
                return False, {"steps": steps, "failed_step": step.name}
            elapsed = round((time.perf_counter() - started) * 1000)
            results = [
                check(
                    a.model_copy(update={"expected": substitute(a.expected, variables)}),
                    resp,
                    elapsed,
                )
                for a in step.assertions
            ]
            record["response"] = {
                "status_code": resp.status_code,
                "elapsed_ms": elapsed,
                "headers": {
                    k: v
                    for k, v in resp.headers.items()
                    if k.lower() in ("content-type", "location", "retry-after")
                },
                "body": summarize_body(resp),
            }
            record["assertions"] = results
            for cap in step.capture:
                try:
                    value = resolve(resp.json(), cap.value)
                except ValueError:
                    value = _MISSING
                if value is not _MISSING:
                    variables[cap.name] = value
                record.setdefault("captured", {})[cap.name] = None if value is _MISSING else value
            if not all(r["passed"] for r in results):
                return False, {"steps": steps, "failed_step": step.name}
        return True, {"steps": steps}
    finally:
        if own:
            client.close()
