"""Drives a real headless Chromium against a tiny page served on 127.0.0.1 (no internet)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from uatpilot.models import UiSpec

pw = pytest.importorskip("playwright.sync_api")

PAGE = b"""<!doctype html><html><body>
<h1 data-test="title">Login</h1>
<input id="user"><button id="go" onclick="
  const ok = document.getElementById('user').value === 'ada';
  document.getElementById('msg').innerText = ok ? 'Welcome ada' : 'Bad user';
">Go</button>
<p id="msg"></p><ul><li class="i">a</li><li class="i">b</li></ul>
</body></html>"""


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _chromium_available() -> bool:
    try:
        with pw.sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_available(), reason="chromium not installed")


def spec(*steps):
    return UiSpec.model_validate({"kind": "ui", "steps": list(steps)})


def test_ui_pass(site, settings):
    from uatpilot.runner.ui import run_ui_spec

    s = spec(
        {"action": "goto", "value": "/"},
        {"action": "fill", "selector": "#user", "value": "ada"},
        {"action": "click", "selector": "#go"},
        {"action": "expect_text", "selector": "#msg", "value": "Welcome"},
        {"action": "expect_count", "selector": ".i", "value": "2"},
    )
    passed, ev = run_ui_spec(s, site, settings, "run_ok")
    assert passed, ev


def test_ui_fail_records_step_and_screenshot(site, settings):
    from uatpilot.runner.ui import run_ui_spec

    s = spec(
        {"action": "goto", "value": "/"},
        {"action": "fill", "selector": "#user", "value": "eve"},
        {"action": "click", "selector": "#go"},
        {"action": "expect_text", "selector": "#msg", "value": "Welcome"},
    )
    settings = settings.__class__(**{**settings.__dict__, "ui_timeout_ms": 1500})
    passed, ev = run_ui_spec(s, site, settings, "run_bad")
    assert not passed
    assert "Bad user" in ev["steps"][-1]["failure"]
    assert ev["screenshot"] == "run_bad.png"
    assert (settings.artifacts_dir / "run_bad.png").exists()


def test_inspect_page_lists_elements(site, settings):
    from uatpilot.runner.ui import inspect_page

    r = inspect_page(site, [], settings)
    ids = {e.get("id") for e in r["elements"]}
    assert {"user", "go"} <= ids
