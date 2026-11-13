import json

import httpx

from uatpilot.models import ApiSpec, Assertion
from uatpilot.runner.api import _MISSING, check, resolve, run_api_spec, substitute
from uatpilot.target import make_client

BASE = "https://restful-booker.herokuapp.com"


def test_resolve_paths():
    data = {"booking": {"firstname": "Ada", "dates": [{"d": 1}]}, "items": [1, 2]}
    assert resolve(data, "booking.firstname") == "Ada"
    assert resolve(data, "booking.dates[0].d") == 1
    assert resolve(data, "items.length()") == 2
    assert resolve([{"id": 7}], "[0].id") == 7
    assert resolve(data, "booking.missing") is _MISSING
    assert resolve(data, "items[5]") is _MISSING


def test_substitute_keeps_types():
    v = {"id": 42, "tok": "abc"}
    assert substitute("{{id}}", v) == 42
    assert substitute("/booking/{{id}}", v) == "/booking/42"
    assert substitute({"h": "token={{tok}}", "n": ["{{id}}"]}, v) == {"h": "token=abc", "n": [42]}
    assert substitute("{{unknown}}", v) == "{{unknown}}"


def _resp(status=200, body=None, headers=None):
    return httpx.Response(status, json=body, headers=headers)


def test_check_operators():
    r = _resp(200, {"a": "hello", "n": 5, "l": [1, 2, 3], "b": True})
    ok = lambda **kw: check(Assertion(**kw), r, 10)["passed"]  # noqa: E731
    assert ok(source="status_code", op="equals", expected="200")  # loose: "200" == 200
    assert ok(source="json", path="a", op="contains", expected="ell")
    assert ok(source="json", path="a", op="matches", expected="^h.*o$")
    assert ok(source="json", path="n", op="greater_than", expected=4)
    assert ok(source="json", path="n", op="type_is", expected="integer")
    assert not ok(source="json", path="b", op="type_is", expected="integer")  # bool isn't int
    assert ok(source="json", path="l", op="length_at_least", expected=3)
    assert ok(source="json", path="b", op="equals", expected=True)
    assert ok(source="json", path="zzz", op="not_exists")
    assert not ok(source="json", path="zzz", op="equals", expected=1)
    assert ok(source="elapsed_ms", op="less_than", expected=3000)


def test_missing_value_is_reported_not_crashed():
    r = _resp(200, {"a": 1})
    res = check(Assertion(source="json", path="b.c", op="greater_than", expected=1), r, 1)
    assert res["passed"] is False and res["actual"] == "<missing>"


def mock_booker() -> httpx.MockTransport:
    state = {"bookings": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/booking":
            body = json.loads(request.content)
            bid = len(state["bookings"]) + 1
            state["bookings"][bid] = body
            return httpx.Response(200, json={"bookingid": bid, "booking": body})
        if request.method == "GET" and path.startswith("/booking/"):
            bid = int(path.rsplit("/", 1)[1])
            if bid in state["bookings"]:
                return httpx.Response(200, json=state["bookings"][bid])
            return httpx.Response(404, text="Not Found")
        if path == "/slow":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(500, text="Internal Server Error")

    return httpx.MockTransport(handler)


def spec(*steps):
    return ApiSpec.model_validate({"kind": "api", "steps": list(steps)})


CREATE = {
    "name": "create",
    "method": "POST",
    "path": "/booking",
    "json_body": {"firstname": "Ada", "totalprice": 10},
    "assertions": [{"source": "status_code", "op": "equals", "expected": 200}],
    "capture": [{"name": "bid", "value": "bookingid"}],
}


def test_chained_steps_with_capture(settings):
    client = make_client(settings, transport=mock_booker())
    s = spec(
        CREATE,
        {
            "name": "fetch",
            "method": "GET",
            "path": "/booking/{{bid}}",
            "assertions": [
                {"source": "status_code", "op": "equals", "expected": 200},
                {"source": "json", "path": "firstname", "op": "equals", "expected": "Ada"},
            ],
        },
    )
    passed, ev = run_api_spec(s, BASE, settings, client)
    assert passed
    assert ev["steps"][0]["captured"] == {"bid": 1}
    assert ev["steps"][1]["request"]["url"].endswith("/booking/1")


def test_failure_stops_at_failing_step(settings):
    client = make_client(settings, transport=mock_booker())
    s = spec(
        {
            "name": "missing",
            "method": "GET",
            "path": "/booking/999",
            "assertions": [{"source": "status_code", "op": "equals", "expected": 200}],
        },
        CREATE,
    )
    passed, ev = run_api_spec(s, BASE, settings, client)
    assert not passed
    assert ev["failed_step"] == "missing"
    assert len(ev["steps"]) == 1
    assert ev["steps"][0]["assertions"][0]["actual"] == 404


def test_timeout_is_recorded_as_error(settings):
    client = make_client(settings, transport=mock_booker())
    s = spec(
        {
            "name": "slow",
            "method": "GET",
            "path": "/slow",
            "assertions": [{"source": "status_code", "op": "equals", "expected": 200}],
        }
    )
    passed, ev = run_api_spec(s, BASE, settings, client)
    assert not passed and ev["steps"][0]["error"]["kind"] == "timeout"


def test_disallowed_host_is_refused(settings):
    from uatpilot.models import TestCase
    from uatpilot.runner import execute

    case = TestCase.model_validate(
        {
            "id": "x",
            "requirement_set_id": "rs",
            "title": "Should not run",
            "requirement_ref": "R",
            "steps": ["x"],
            "expected_result": "nothing",
            "priority": "low",
            "executable": spec(CREATE).model_dump(),
        }
    )
    passed, _, ev = execute(case, "https://internal.example.com", settings, "run_1")
    assert not passed and ev["error"]["kind"] == "not_allowed"
