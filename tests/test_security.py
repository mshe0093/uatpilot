"""Security hardening: headers, token check, CSV formula injection, the browser allowlist, and
the generator's check for URL assertions that can never fail."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient
from test_api import load_seed

from uatpilot.api import create_app
from uatpilot.config import Settings
from uatpilot.export import _cell, to_csv
from uatpilot.generator import Generator
from uatpilot.models import TargetKind, TestSuite
from uatpilot.runner.ui import _is_local


def test_security_headers_on_api_and_pages(tmp_path):
    c = TestClient(create_app(Settings(db_path=tmp_path / "t.db", engine="rules")))
    r = c.get("/api/health", headers={"x-forwarded-proto": "https"})
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in r.headers["strict-transport-security"]


def test_write_token_must_match_exactly(tmp_path):
    s = Settings(db_path=tmp_path / "t.db", engine="rules", write_token="sekret")
    c = TestClient(create_app(s))
    set_id = load_seed(c.app.state.store)
    for bad in ("Bearer sekre", "Bearer sekret ", "sekret", "Bearer SEKRET", ""):
        r = c.post("/api/run", json={"requirement_set_id": set_id}, headers={"Authorization": bad})
        assert r.status_code == 401, bad


def test_negative_limit_is_clamped(tmp_path):
    c = TestClient(create_app(Settings(db_path=tmp_path / "t.db", engine="rules")))
    assert c.get("/api/batches?limit=-1").status_code == 200


def test_path_and_sql_injection_attempts_are_inert(tmp_path):
    c = TestClient(create_app(Settings(db_path=tmp_path / "t.db", engine="rules")))
    set_id = load_seed(c.app.state.store)
    for bad in ("x' OR '1'='1", "1; DROP TABLE runs;--", "../../etc/passwd"):
        assert c.get(f"/api/requirement-sets/{bad}").status_code == 404
        assert c.get("/api/batches", params={"requirement_set_id": bad}).json() == []
    assert c.get(f"/api/requirement-sets/{set_id}").status_code == 200  # tables intact
    r = c.post("/api/run", json={"requirement_set_id": "rs_x' OR 1=1"})
    assert r.status_code == 422  # ids are pattern-checked before any lookup


def test_csv_cells_cannot_run_formulas(tmp_path):
    assert _cell('=HYPERLINK("http://evil")') == '\'=HYPERLINK("http://evil")'
    for prefix in "+-@\t\r":
        assert _cell(prefix + "1").startswith("'")
    assert _cell("Login works") == "Login works" and _cell(3) == 3

    c = TestClient(create_app(Settings(db_path=tmp_path / "t.db", engine="rules")))
    store = c.app.state.store
    set_id = load_seed(store)
    case = store.test_cases(set_id)[0]
    store._conn.execute(
        "UPDATE test_cases SET title = ? WHERE id = ?", ("=cmd|' /C calc'!A0", case.id)
    )
    store._conn.commit()
    rows = list(csv.reader(io.StringIO(to_csv(store, set_id))))
    assert all(not cell.startswith("=") for row in rows for cell in row)


def test_browser_guard_spots_local_and_private_hosts():
    for url in (
        "http://localhost:8000/api/run",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "http://metadata.google.internal/",
    ):
        assert _is_local(url), url
    for url in ("https://www.saucedemo.com/", "https://fonts.gstatic.com/x.woff2"):
        assert not _is_local(url), url


def _ui_suite(expect_url: str) -> TestSuite:
    return TestSuite.model_validate(
        {
            "test_cases": [
                {
                    "title": "Logout returns to login",
                    "requirement_ref": "AC-5.1",
                    "steps": ["log out"],
                    "expected_result": "login page",
                    "priority": "high",
                    "executable": {
                        "kind": "ui",
                        "steps": [
                            {"action": "goto", "value": "/"},
                            {"action": "expect_url", "value": expect_url},
                        ],
                    },
                }
            ]
        }
    )


def test_url_checks_that_match_every_page_are_rejected(tmp_path):
    g = Generator(
        base_url="https://www.saucedemo.com",
        target_kind=TargetKind.UI,
        client=object(),
        settings=Settings(db_path=tmp_path / "t.db"),
    )
    for vacuous in (
        "https://www.saucedemo.com/",
        "https://www.saucedemo.com",
        "saucedemo.com",
        "/",
    ):
        assert g._vacuous_url_checks(_ui_suite(vacuous)), vacuous
    assert not g._vacuous_url_checks(_ui_suite("/inventory.html"))
