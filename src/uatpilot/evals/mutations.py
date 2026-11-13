"""The labelled bug catalogue for the Restful-Booker requirements doc.

Each mutation is one deliberately injected defect with the acceptance criterion it violates. The
eval counts a mutation as *detected* if at least one test case passes on the clean baseline and
fails with the mutation active. A mutation whose criterion is already broken by the real sandbox
(e.g. AC-4.2, which the sandbox fails natively) can be masked: the eval reports that separately
instead of pretending it was caught.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from uatpilot.evals.proxy import Exchange, Mode, Reply

_ID = re.compile(r"^/booking/(\d+)$")


@dataclass(frozen=True)
class Mutation:
    name: str
    breaks: str  # acceptance criterion id(s)
    description: str
    mode: Mode


def _post(match_method: str, match: re.Pattern[str] | str, fn) -> Mode:
    def post(ex: Exchange, r: Reply) -> Reply:
        hit = ex.method == match_method and (
            match.match(ex.path) if isinstance(match, re.Pattern) else ex.path == match
        )
        if not hit or r.status >= 400:
            return r
        body = copy.deepcopy(r.body)
        out = fn(ex, body)
        return out if isinstance(out, Reply) else Reply(r.status, body)

    return Mode(post=post)


def _pre(fn) -> Mode:
    return Mode(pre=fn)


def _set(path: str, value: Any):
    def f(_ex: Exchange, body: Any) -> None:
        node = body
        *parents, last = path.split(".")
        for p in parents:
            node = node.get(p) if isinstance(node, dict) else None
        if isinstance(node, dict) and last in node:
            if value is _DROP:
                del node[last]
            else:
                node[last] = value(node[last]) if callable(value) else value

    return f


_DROP = object()
_is_list_query = lambda ex: ex.method == "GET" and ex.path == "/booking"  # noqa: E731


def _no_token(ex: Exchange) -> bool:
    return "token=" not in ex.headers.get("cookie", "") and "authorization" not in ex.headers


CATALOGUE: list[Mutation] = [
    Mutation(
        "ping_500",
        "AC-1.1",
        "Health endpoint returns 500.",
        _pre(lambda ex: Reply(500, "Internal Server Error") if ex.path == "/ping" else None),
    ),
    Mutation(
        "create_drops_bookingid",
        "AC-2.1",
        "Create response omits bookingid.",
        _post("POST", "/booking", _set("bookingid", _DROP)),
    ),
    Mutation(
        "create_echo_wrong_lastname",
        "AC-2.1",
        "Create response echoes the last name in upper case.",
        _post("POST", "/booking", _set("booking.lastname", lambda v: str(v).upper())),
    ),
    Mutation(
        "create_price_off_by_one",
        "AC-2.1",
        "Create response echoes totalprice + 1.",
        _post("POST", "/booking", _set("booking.totalprice", lambda v: v + 1)),
    ),
    Mutation(
        "create_depositpaid_flipped",
        "AC-2.1",
        "Create response flips depositpaid.",
        _post("POST", "/booking", _set("booking.depositpaid", lambda v: not v)),
    ),
    Mutation(
        "create_drops_additionalneeds",
        "AC-2.1",
        "Create response drops additionalneeds.",
        _post("POST", "/booking", _set("booking.additionalneeds", _DROP)),
    ),
    Mutation(
        "get_wrong_firstname",
        "AC-2.2",
        "GET /booking/{id} returns the first name reversed.",
        _post("GET", _ID, _set("firstname", lambda v: str(v)[::-1])),
    ),
    Mutation(
        "get_wrong_price",
        "AC-2.2",
        "GET /booking/{id} returns totalprice doubled.",
        _post("GET", _ID, _set("totalprice", lambda v: v * 2)),
    ),
    Mutation(
        "get_drops_checkout",
        "AC-2.2",
        "GET /booking/{id} omits bookingdates.checkout.",
        _post("GET", _ID, _set("bookingdates.checkout", _DROP)),
    ),
    Mutation(
        "new_booking_not_found",
        "AC-2.2",
        "Freshly created bookings 404 on GET.",
        _pre(
            lambda ex: (
                Reply(404, "Not Found")
                if ex.method == "GET"
                and _ID.match(ex.path)
                and int(_ID.match(ex.path).group(1)) < 10**8
                else None
            )
        ),
    ),
    Mutation(
        "list_returns_object",
        "AC-3.1",
        "GET /booking returns an object instead of an array.",
        _post("GET", "/booking", lambda ex, b: Reply(200, {"bookings": b})),
    ),
    Mutation(
        "filter_ignored_empty",
        "AC-3.2",
        "Name filter always returns an empty list.",
        _post("GET", "/booking", lambda ex, b: Reply(200, []) if ex.query else None),
    ),
    Mutation(
        "missing_booking_200",
        "AC-3.3",
        "Unknown booking ids return 200 {} instead of 404.",
        Mode(
            post=lambda ex, r: (
                Reply(200, {})
                if ex.method == "GET" and _ID.match(ex.path) and r.status == 404
                else r
            )
        ),
    ),
    Mutation(
        "auth_no_token",
        "AC-4.1",
        "Valid credentials no longer return a token.",
        _post("POST", "/auth", lambda ex, b: Reply(200, {"reason": "Bad credentials"})),
    ),
    Mutation(
        "put_without_token_allowed",
        "AC-4.3",
        "PUT without a token succeeds (200).",
        _pre(lambda ex: Reply(200, ex.body) if ex.method == "PUT" and _no_token(ex) else None),
    ),
    Mutation(
        "delete_without_token_allowed",
        "AC-4.3",
        "DELETE without a token succeeds (201).",
        _pre(
            lambda ex: Reply(201, "Created") if ex.method == "DELETE" and _no_token(ex) else None
        ),
    ),
    Mutation(
        "put_ignores_update",
        "AC-5.1",
        "PUT responds 200 but echoes the old first name.",
        _post("PUT", _ID, _set("firstname", "Old")),
    ),
    Mutation(
        "patch_clobbers_lastname",
        "AC-5.2",
        "PATCH wipes the last name.",
        _post("PATCH", _ID, _set("lastname", None)),
    ),
    Mutation(
        "delete_is_noop",
        "AC-6.2",
        "DELETE reports success but the booking still exists.",
        _pre(
            lambda ex: (
                Reply(201, "Created") if ex.method == "DELETE" and not _no_token(ex) else None
            )
        ),
    ),
    Mutation(
        "slow_reads",
        "NFR-1",
        "Single-booking reads take 3.5 s.",
        Mode(
            pre=lambda ex: (
                __import__("time").sleep(3.5)
                if ex.method == "GET" and _ID.match(ex.path)
                else None
            )
        ),
    ),
]


# ---- triage eval: failures with a known correct label --------------------------------------------
@dataclass(frozen=True)
class TriageScenario:
    name: str
    expected: str  # real_regression | flaky | environment_issue
    description: str

    def mode(self) -> Mode:
        match self.name:
            case "flaky_once":
                # The very first request of the run gets a corrupted (wrong-status) reply; the
                # immediate re-run passes. Budget 1 = misbehave once only.
                return Mode(pre=lambda ex: Reply(418, "I'm a teapot"), budget=1)
            case "env_503":
                return Mode(pre=lambda ex: Reply(503, "Service Unavailable"))
            case "env_timeout":
                return Mode(delay_s=4.0)  # eval sets http_timeout below this
            case "env_429":
                return Mode(pre=lambda ex: Reply(429, "Too Many Requests", {"Retry-After": "30"}))
            case "real_wrong_status":
                return Mode(post=lambda ex, r: Reply(409, r.body) if r.status < 300 else r)
            case _:
                raise ValueError(self.name)


TRIAGE_SCENARIOS = [
    TriageScenario("flaky_once", "flaky", "First request fails once, re-run passes."),
    TriageScenario("env_503", "environment_issue", "Every request gets 503."),
    TriageScenario("env_timeout", "environment_issue", "Every request hangs past the timeout."),
    TriageScenario("env_429", "environment_issue", "Every request is rate limited (429)."),
    TriageScenario(
        "real_wrong_status",
        "real_regression",
        "Every 2xx becomes 409 Conflict: consistent wrong behaviour.",
    ),
]
