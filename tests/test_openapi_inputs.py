"""OpenAPI input paths: CLI --openapi (file, allowlisted URL, refused URL) and the API field."""

import csv
import io

import pytest
import typer
from fastapi.testclient import TestClient

from uatpilot import api as api_module
from uatpilot.api import create_app
from uatpilot.cli import _load_spec
from uatpilot.config import Settings

SPEC = """
openapi: 3.0.0
info: {title: t, version: "1"}
paths:
  /ping:
    get:
      responses:
        "201": {description: created}
"""


def test_cli_loads_spec_from_file(tmp_path, settings):
    f = tmp_path / "openapi.yaml"
    f.write_text(SPEC)
    assert '"GET /ping"' in _load_spec(str(f), settings)


def test_cli_refuses_spec_url_off_the_allowlist(settings):
    with pytest.raises(typer.BadParameter, match="ALLOWED_HOSTS"):
        _load_spec("https://internal.example.com/openapi.json", settings)


def test_cli_reports_unreadable_spec(tmp_path, settings):
    f = tmp_path / "x.json"
    f.write_text('{"not": "a spec"}')
    with pytest.raises(typer.BadParameter, match="Not an OpenAPI"):
        _load_spec(str(f), settings)


def test_api_rejects_bad_spec_before_calling_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "has_claude_credentials", lambda: True)
    app = create_app(Settings(db_path=tmp_path / "t.db", engine="auto"))
    r = TestClient(app).post(
        "/api/generate",
        json={
            "source_name": "r.md",
            "requirements": "AC-1.1 ping works and returns created",
            "base_url": "https://restful-booker.herokuapp.com",
            "target_kind": "api",
            "openapi": "this: is: not: yaml: [",
        },
    )
    assert r.status_code == 400 and "OpenAPI" in r.json()["detail"]


def test_csv_has_change_column(store):
    from test_pipeline import load_seed

    from uatpilot.export import to_csv

    set_id = load_seed(store)
    header = next(csv.reader(io.StringIO(to_csv(store, set_id))))
    assert header[header.index("status") + 1] == "since_last_run"
