from pathlib import Path

from typer.testing import CliRunner

from uatpilot.cli import app

ROOT = Path(__file__).resolve().parents[1]


def test_import_list_export(tmp_path, monkeypatch):
    monkeypatch.setenv("UATPILOT_DB", str(tmp_path / "cli.db"))
    runner = CliRunner()
    r = runner.invoke(app, ["import", str(ROOT / "examples/suites/restful-booker.seed.json")])
    assert r.exit_code == 0, r.output
    set_id = r.output.split(" as ")[-1].strip()
    assert set_id.startswith("rs_")
    assert runner.invoke(app, ["list"]).exit_code == 0
    out = tmp_path / "signoff.md"
    r = runner.invoke(app, ["export", set_id, "--format", "md", "--out", str(out)])
    assert r.exit_code == 0 and "UAT Sign-off" in out.read_text("utf-8")


def test_generate_refuses_disallowed_host(tmp_path):
    req = tmp_path / "r.md"
    req.write_text("AC-1 something")
    r = CliRunner().invoke(app, ["generate", str(req), "--base-url", "https://example.com"])
    assert r.exit_code != 0


def test_generate_needs_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    req = tmp_path / "r.md"
    req.write_text("AC-1 something")
    r = CliRunner().invoke(
        app, ["generate", str(req), "--base-url", "https://restful-booker.herokuapp.com"]
    )
    assert r.exit_code == 2
