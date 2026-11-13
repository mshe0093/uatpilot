import json
from pathlib import Path

import httpx

from uatpilot import pipeline, runner
from uatpilot.export import to_csv, to_markdown
from uatpilot.models import TestSuite

ROOT = Path(__file__).resolve().parents[1]


def load_seed(store, name="restful-booker"):
    doc = json.loads((ROOT / "examples" / "suites" / f"{name}.seed.json").read_text("utf-8"))
    suite = TestSuite.model_validate({"test_cases": doc["test_cases"]})
    return store.save_suite(
        source_name=f"{name}.md",
        raw_text="reqs",
        target_kind=doc["target_kind"],
        base_url=doc["base_url"],
        generated_by="import:test",
        drafts=suite.test_cases,
    )


def test_seed_suites_validate():
    for name in ("restful-booker", "saucedemo"):
        doc = json.loads((ROOT / "examples" / "suites" / f"{name}.seed.json").read_text("utf-8"))
        assert TestSuite.model_validate({"test_cases": doc["test_cases"]}).test_cases


def test_run_suite_stores_runs_and_triage(store, settings, monkeypatch):
    """Ping passes; everything else hits a 500, which must be FAIL with a triage label."""

    def handler(request):
        if request.url.path == "/ping":
            return httpx.Response(201, text="Created")
        return httpx.Response(500, text="boom")

    real_execute = runner.execute

    def fake_execute(case, base_url, s, run_id, http_client=None):
        from uatpilot.target import make_client

        return real_execute(
            case, base_url, s, run_id, make_client(s, transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    set_id = load_seed(store)
    events = list(pipeline.run_suite(store, set_id, settings))
    done = events[-1]
    assert done["type"] == "batch_done" and done["pass"] == 1 and done["fail"] == 15
    runs = store.batch_runs(done["batch_id"])
    assert len(runs) == 16
    failed = [r for r in runs if r["status"] == "fail"]
    assert all(r["triage_label"] for r in failed)  # every failure is explained
    assert all(r["status"] == "fail" for r in failed)  # and none was flipped to pass
    assert all("rerun" in r["evidence"] for r in failed)

    batches = store.batches(set_id)
    assert batches[0]["passed"] == 1 and batches[0]["failed"] == 15
    rate = store.flaky_rate(set_id)
    assert rate["runs"] == 16 and rate["flaky"] == 0

    md = to_markdown(store, set_id)
    assert "# UAT Sign-off" in md and "1/16 passed" in md and "Tester sign-off" in md
    csv_text = to_csv(store, set_id)
    assert csv_text.count("\n") == 17


def test_flaky_case_is_labelled_flaky_but_still_fails(store, settings, monkeypatch):
    calls = {"n": 0}

    def fake_execute(case, base_url, s, run_id, http_client=None):
        calls["n"] += 1
        if calls["n"] == 1:  # first run fails, immediate re-run passes
            return (
                False,
                5,
                {
                    "steps": [
                        {
                            "name": "ping",
                            "response": {"status_code": 404},
                            "assertions": [
                                {
                                    "source": "status_code",
                                    "op": "equals",
                                    "expected": 200,
                                    "passed": False,
                                    "actual": 404,
                                }
                            ],
                        }
                    ]
                },
            )
        return True, 5, {"steps": []}

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    set_id = load_seed(store)
    first = store.test_cases(set_id)[0].id
    events = list(pipeline.run_suite(store, set_id, settings, case_ids=[first]))
    run = next(e for e in events if e["type"] == "case_result")["run"]
    assert run["status"] == "fail"
    assert run["triage"]["label"] == "flaky"
