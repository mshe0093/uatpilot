"""The eval harness end to end, offline: proxy -> a tiny local fake booking API."""

import threading
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from uatpilot.evals.harness import run_eval, write_report
from uatpilot.evals.mutations import CATALOGUE, TRIAGE_SCENARIOS
from uatpilot.models import TestSuite

BOOKINGS: dict[int, dict] = {}


async def ping(_r: Request):
    return PlainTextResponse("Created", 201)


async def booking(r: Request):
    if r.method == "POST":
        body = await r.json()
        bid = len(BOOKINGS) + 1
        BOOKINGS[bid] = body
        return JSONResponse({"bookingid": bid, "booking": body})
    bid = int(r.path_params["bid"])
    if bid in BOOKINGS:
        return JSONResponse(BOOKINGS[bid])
    return PlainTextResponse("Not Found", 404)


@pytest.fixture(scope="module")
def fake_api():
    app = Starlette(
        routes=[
            Route("/ping", ping),
            Route("/booking", booking, methods=["POST"]),
            Route("/booking/{bid:int}", booking),
        ]
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    server.should_exit = True


SUITE = [
    {
        "title": "Ping is up",
        "requirement_ref": "AC-1.1",
        "steps": ["GET /ping"],
        "expected_result": "2xx",
        "priority": "high",
        "executable": {
            "kind": "api",
            "steps": [
                {
                    "name": "ping",
                    "method": "GET",
                    "path": "/ping",
                    "assertions": [{"source": "status_code", "op": "less_than", "expected": 300}],
                }
            ],
        },
    },
    {
        "title": "Create then fetch",
        "requirement_ref": "AC-2.2",
        "steps": ["create", "fetch"],
        "expected_result": "same data",
        "priority": "high",
        "executable": {
            "kind": "api",
            "steps": [
                {
                    "name": "create",
                    "method": "POST",
                    "path": "/booking",
                    "json_body": {"firstname": "Ada", "totalprice": 10},
                    "assertions": [{"source": "status_code", "op": "equals", "expected": 200}],
                    "capture": [{"name": "bid", "value": "bookingid"}],
                },
                {
                    "name": "fetch",
                    "method": "GET",
                    "path": "/booking/{{bid}}",
                    "assertions": [
                        {"source": "json", "path": "firstname", "op": "equals", "expected": "Ada"},
                        {"source": "json", "path": "totalprice", "op": "equals", "expected": 10},
                    ],
                },
            ],
        },
    },
]


def test_eval_end_to_end(fake_api, store, settings, tmp_path):
    set_id = store.save_suite(
        source_name="fake.md",
        raw_text="x",
        target_kind="api",
        base_url=fake_api,
        generated_by="import:test",
        drafts=TestSuite.model_validate({"test_cases": SUITE}).test_cases,
    )
    pick = {"ping_500", "get_wrong_price", "auth_no_token"}  # last one: nothing covers AC-4.1
    report = run_eval(
        store,
        set_id,
        settings,
        mutations=[m for m in CATALOGUE if m.name in pick],
        scenarios=[s for s in TRIAGE_SCENARIOS if s.name in ("flaky_once", "env_503")],
        triage_cases=1,
        log=lambda s: None,
    )
    rows = {r["mutation"]: r["status"] for r in report["detection"]["rows"]}
    assert rows == {
        "ping_500": "detected",
        "get_wrong_price": "detected",
        "auth_no_token": "masked",
    }
    assert report["detection"]["rate"] == 1.0
    assert report["triage"]["accuracy"] == 1.0
    assert report["triage"]["never_flipped_to_pass"] is True
    j, m = write_report(report, tmp_path)
    assert "Detection rate" in m.read_text("utf-8") and j.exists()
