import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_pipeline import load_seed

from uatpilot import pipeline, runner
from uatpilot.api import create_app
from uatpilot.config import Settings


@pytest.fixture
def app(settings, monkeypatch):
    def handler(request):
        return httpx.Response(201 if request.url.path == "/ping" else 500, text="x")

    real = runner.execute

    def fake_execute(case, base_url, s, run_id, http_client=None):
        from uatpilot.target import make_client

        return real(
            case, base_url, s, run_id, make_client(s, transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    return create_app(settings)


def frames(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_health_and_reads(app):
    c = TestClient(app)
    h = c.get("/api/health").json()
    assert h["status"] == "ok" and h["generation_available"] is False
    assert c.get("/api/health").headers["x-content-type-options"] == "nosniff"
    set_id = load_seed(app.state.store)
    assert c.get("/api/requirement-sets").json()[0]["case_count"] == 16
    detail = c.get(f"/api/requirement-sets/{set_id}").json()
    assert len(detail["test_cases"]) == 16 and detail["test_cases"][0]["latest_run"] is None
    assert c.get("/api/requirement-sets/rs_nope").status_code == 404


def test_run_streams_and_exports(app):
    c = TestClient(app)
    set_id = load_seed(app.state.store)
    r = c.post("/api/run", json={"requirement_set_id": set_id})
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = frames(r.text)
    assert evs[0]["type"] == "batch_start" and evs[-1]["type"] == "batch_done"
    batches = c.get(f"/api/batches?requirement_set_id={set_id}").json()
    assert batches[-1]["passed"] == 1
    runs = c.get(f"/api/batches/{batches[-1]['id']}").json()["runs"]
    assert len(runs) == 16
    md = c.get(f"/api/requirement-sets/{set_id}/export.md")
    assert md.status_code == 200 and "UAT Sign-off" in md.text
    assert "attachment" in md.headers["content-disposition"]
    assert c.get(f"/api/requirement-sets/{set_id}/export.csv").text.startswith("id,")


def test_generate_without_credentials_is_503(app):
    c = TestClient(app)
    r = c.post(
        "/api/generate",
        json={
            "source_name": "x.md",
            "requirements": "x" * 30,
            "base_url": "https://restful-booker.herokuapp.com",
            "target_kind": "api",
        },
    )
    assert r.status_code == 503


def test_write_token_makes_it_read_only(tmp_path):
    s = Settings(db_path=tmp_path / "t.db", engine="rules", write_token="sekret")
    c = TestClient(create_app(s))
    set_id = load_seed(c.app.state.store)
    assert c.post("/api/run", json={"requirement_set_id": set_id}).status_code == 401


def test_artifact_name_is_validated(app):
    c = TestClient(app)
    assert c.get("/api/artifacts/..%2Fuatpilot.db").status_code == 404
    assert c.get("/api/artifacts/run_000000000000.png").status_code == 404
