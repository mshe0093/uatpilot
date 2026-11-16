"""Exporting a suite as a seed file and importing it again keeps every case and its author."""

from __future__ import annotations

import json
from pathlib import Path

from test_pipeline import ROOT, load_seed
from typer.testing import CliRunner

from uatpilot.cli import app
from uatpilot.db import Store
from uatpilot.export import to_seed
from uatpilot.models import TestSuite


def test_seed_round_trip_keeps_cases_and_author(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("UATPILOT_DB", str(db))
    monkeypatch.setenv("UATPILOT_NO_DOTENV", "1")
    store = Store(db)
    set_id = load_seed(store)
    store._conn.execute(
        "UPDATE requirement_sets SET generated_by = ?, source_name = ? WHERE id = ?",
        ("gemini:gemini-3.5-flash", "restful-booker.md", set_id),
    )
    store._conn.execute(
        "UPDATE requirement_sets SET coverage_json = ? WHERE id = ?",
        (json.dumps({"not_covered": {"NFR-1": "Needs a load test."}}), set_id),
    )
    store._conn.commit()

    doc = json.loads(to_seed(store, set_id))
    assert doc["not_covered"] == [{"requirement_ref": "NFR-1", "reason": "Needs a load test."}]
    assert doc["generated_by"] == "gemini:gemini-3.5-flash"
    assert doc["requirements"] == "../requirements/restful-booker.md"
    TestSuite.model_validate({"test_cases": doc["test_cases"]})  # importable as is

    seed = ROOT / "examples" / "suites" / "roundtrip.test.json"
    try:
        seed.write_text(json.dumps(doc), encoding="utf-8")
        r = CliRunner().invoke(app, ["import", str(seed)])
        assert r.exit_code == 0, r.output
    finally:
        seed.unlink(missing_ok=True)

    sets = Store(db).requirement_sets()
    new = next(s for s in sets if s["id"] != set_id)
    assert new["generated_by"] == "gemini:gemini-3.5-flash"
    before = [c.model_dump(exclude={"id", "requirement_set_id"}) for c in store.test_cases(set_id)]
    after = [
        c.model_dump(exclude={"id", "requirement_set_id"}) for c in Store(db).test_cases(new["id"])
    ]
    assert before == after


def test_every_shipped_seed_validates():
    seeds = sorted(Path(ROOT / "examples" / "suites").glob("*.seed.json"))
    assert len(seeds) >= 2
    for path in seeds:
        doc = json.loads(path.read_text("utf-8"))
        assert TestSuite.model_validate({"test_cases": doc["test_cases"]}).test_cases, path.name
        assert (path.parent / doc["requirements"]).is_file(), path.name


def test_excused_criteria_survive_the_round_trip(tmp_path, monkeypatch):
    """A criterion the agent declared not coverable stays excused (with its reason) after import,
    instead of turning into a silent gap."""
    db = tmp_path / "t.db"
    monkeypatch.setenv("UATPILOT_DB", str(db))
    monkeypatch.setenv("UATPILOT_NO_DOTENV", "1")
    doc = json.loads(
        (ROOT / "examples" / "suites" / "restful-booker.seed.json").read_text("utf-8")
    )
    doc["test_cases"] = [c for c in doc["test_cases"] if c["requirement_ref"] != "NFR-1"]
    doc["not_covered"] = [{"requirement_ref": "NFR-1", "reason": "Needs a load test."}]
    seed = ROOT / "examples" / "suites" / "excused.test.json"
    try:
        seed.write_text(json.dumps(doc), encoding="utf-8")
        assert CliRunner().invoke(app, ["import", str(seed)]).exit_code == 0
    finally:
        seed.unlink(missing_ok=True)
    store = Store(db)
    cov = store.requirement_set(store.requirement_sets()[0]["id"])["coverage"]
    assert cov["not_covered"] == {"NFR-1": "Needs a load test."} and cov["missing"] == []
    assert json.loads(to_seed(store, store.requirement_sets()[0]["id"]))["not_covered"] == [
        {"requirement_ref": "NFR-1", "reason": "Needs a load test."}
    ]
