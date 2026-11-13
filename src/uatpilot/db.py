"""SQLite storage. The three tables from the project plan, plus `run_batches` so a dashboard can
group the runs from one "Run suite" click and draw a trend over time."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uatpilot.models import RunResult, TestCase, TestCaseDraft, Triage

SCHEMA = """
CREATE TABLE IF NOT EXISTS requirement_sets (
  id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  target_kind TEXT NOT NULL,          -- 'api' | 'ui'
  base_url TEXT NOT NULL,
  generated_by TEXT NOT NULL,         -- 'claude:<model>' | 'import:<file>'
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS test_cases (
  id TEXT PRIMARY KEY,
  requirement_set_id TEXT NOT NULL REFERENCES requirement_sets(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  title TEXT NOT NULL,
  requirement_ref TEXT NOT NULL,
  preconditions TEXT NOT NULL,        -- JSON list
  steps_json TEXT NOT NULL,           -- ordered list
  expected_result TEXT NOT NULL,
  priority TEXT NOT NULL,             -- high / medium / low
  target_kind TEXT NOT NULL,          -- 'api' | 'ui'
  executable_spec_json TEXT NOT NULL  -- request/assertion the runner understands
);

CREATE TABLE IF NOT EXISTS run_batches (
  id TEXT PRIMARY KEY,
  requirement_set_id TEXT NOT NULL REFERENCES requirement_sets(id) ON DELETE CASCADE,
  started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  base_url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  test_case_id TEXT NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
  batch_id TEXT NOT NULL REFERENCES run_batches(id) ON DELETE CASCADE,
  run_at TIMESTAMP NOT NULL,
  status TEXT NOT NULL,               -- pass / fail (never overwritten by triage)
  duration_ms INTEGER NOT NULL,
  evidence_json TEXT NOT NULL,        -- response diff, screenshot path, error
  triage_label TEXT,                  -- real_regression / flaky / environment_issue / null
  triage_explanation TEXT,
  triage_json TEXT                    -- full triage record (engine, confidence, floor)
);

CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_cases_set ON test_cases(requirement_set_id);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive, idempotent column migrations for databases created by older versions."""
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    def close(self) -> None:
        self._conn.close()

    # ---- requirement sets + cases ----------------------------------------------------------
    def save_suite(
        self,
        *,
        source_name: str,
        raw_text: str,
        target_kind: str,
        base_url: str,
        generated_by: str,
        drafts: list[TestCaseDraft],
        coverage: dict[str, Any] | None = None,
    ) -> str:
        set_id = new_id("rs")
        with self.tx() as c:
            c.execute(
                "INSERT INTO requirement_sets (id, source_name, raw_text, target_kind, base_url, "
                "generated_by, created_at, coverage_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    set_id,
                    source_name,
                    raw_text,
                    target_kind,
                    base_url,
                    generated_by,
                    now(),
                    json.dumps(coverage) if coverage is not None else None,
                ),
            )
            for i, d in enumerate(drafts, start=1):
                c.execute(
                    "INSERT INTO test_cases (id, requirement_set_id, seq, title, requirement_ref, "
                    "preconditions, steps_json, expected_result, priority, target_kind, "
                    "executable_spec_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"{set_id}-TC{i:03d}",
                        set_id,
                        i,
                        d.title,
                        d.requirement_ref,
                        json.dumps(d.preconditions),
                        json.dumps(d.steps),
                        d.expected_result,
                        d.priority.value,
                        d.target_kind.value,
                        d.executable.model_dump_json(),
                    ),
                )
        return set_id

    def requirement_sets(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT rs.id, rs.source_name, rs.target_kind, rs.base_url, rs.generated_by,
                      rs.created_at, COUNT(tc.id) AS case_count
               FROM requirement_sets rs LEFT JOIN test_cases tc ON tc.requirement_set_id = rs.id
               GROUP BY rs.id ORDER BY rs.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def requirement_set(self, set_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM requirement_sets WHERE id = ?", (set_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        raw = d.pop("coverage_json", None)
        d["coverage"] = json.loads(raw) if raw else None
        return d

    def test_cases(self, set_id: str) -> list[TestCase]:
        rows = self._conn.execute(
            "SELECT * FROM test_cases WHERE requirement_set_id = ? ORDER BY seq", (set_id,)
        ).fetchall()
        return [self._case(r) for r in rows]

    @staticmethod
    def _case(r: sqlite3.Row) -> TestCase:
        return TestCase.model_validate(
            {
                "id": r["id"],
                "requirement_set_id": r["requirement_set_id"],
                "title": r["title"],
                "requirement_ref": r["requirement_ref"],
                "preconditions": json.loads(r["preconditions"]),
                "steps": json.loads(r["steps_json"]),
                "expected_result": r["expected_result"],
                "priority": r["priority"],
                "executable": json.loads(r["executable_spec_json"]),
            }
        )

    def delete_requirement_set(self, set_id: str) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM requirement_sets WHERE id = ?", (set_id,)).rowcount > 0

    # ---- batches + runs ----------------------------------------------------------------------
    def start_batch(self, set_id: str, base_url: str) -> str:
        batch_id = new_id("batch")
        with self.tx() as c:
            c.execute(
                "INSERT INTO run_batches (id, requirement_set_id, started_at, base_url) "
                "VALUES (?,?,?,?)",
                (batch_id, set_id, now(), base_url),
            )
        return batch_id

    def finish_batch(self, batch_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE run_batches SET finished_at = ? WHERE id = ?", (now(), batch_id))

    def save_run(self, run: RunResult) -> None:
        t = run.triage
        with self.tx() as c:
            c.execute(
                "INSERT INTO runs (id, test_case_id, batch_id, run_at, status, duration_ms, "
                "evidence_json, triage_label, triage_explanation, triage_json, signature) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.test_case_id,
                    run.batch_id,
                    run.run_at.isoformat(timespec="seconds"),
                    run.status.value,
                    run.duration_ms,
                    json.dumps(run.evidence, default=str),
                    t.label.value if t else None,
                    t.explanation if t else None,
                    t.model_dump_json() if t else None,
                    run.signature,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["evidence"] = json.loads(d.pop("evidence_json"))
        d["triage"] = json.loads(d.pop("triage_json")) if d.get("triage_json") else None
        return d

    def run_matrix(self, set_id: str, limit: int = 24) -> dict[str, Any]:
        """Cases x the last `limit` batches (oldest first): the dashboard's run matrix."""
        batches = list(reversed(self.batches(set_id, limit)))
        ids = [b["id"] for b in batches]
        cells: dict[tuple[str, str], dict[str, Any]] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for r in self._conn.execute(
                f"SELECT id, test_case_id, batch_id, status, triage_label FROM runs "  # noqa: S608
                f"WHERE batch_id IN ({marks})",
                ids,
            ):
                cells[(r["test_case_id"], r["batch_id"])] = {
                    "run_id": r["id"],
                    "status": r["status"],
                    "label": r["triage_label"],
                }
        rows = [
            {
                "case_id": c.id,
                "title": c.title,
                "requirement_ref": c.requirement_ref,
                "cells": [cells.get((c.id, b)) for b in ids],
            }
            for c in self.test_cases(set_id)
        ]
        return {
            "batches": [
                {
                    "id": b["id"],
                    "started_at": b["started_at"],
                    "passed": b["passed"],
                    "total": b["total"],
                }
                for b in batches
            ],
            "rows": rows,
        }

    # ---- history, reuse and human review ---------------------------------------------------
    def case_history(self, case_id: str, limit: int = 10) -> list[str]:
        """Previous statuses of one case, newest first."""
        rows = self._conn.execute(
            "SELECT status FROM runs WHERE test_case_id = ? ORDER BY run_at DESC, rowid DESC "
            "LIMIT ?",
            (case_id, limit),
        ).fetchall()
        return [r["status"] for r in rows]

    def reusable_triage(self, case_id: str, signature: str) -> dict[str, Any] | None:
        """The best earlier decision for the same failure of the same case: a reviewer's label
        first (ReportPortal weights manual analysis highest), then the newest model decision.
        Floored and reused decisions are not reused again, to avoid chains of copies."""
        rows = self._conn.execute(
            "SELECT id, triage_json FROM runs WHERE test_case_id = ? AND signature = ? "
            "AND triage_json IS NOT NULL ORDER BY run_at DESC, rowid DESC LIMIT 50",
            (case_id, signature),
        ).fetchall()
        candidates = []
        for r in rows:
            t = json.loads(r["triage_json"])
            if t.get("engine") in ("human", "llm", "claude") and not t.get("floor_applied"):
                candidates.append({**t, "run_id": r["id"]})
        humans = [c for c in candidates if c["engine"] == "human"]
        return (humans or candidates or [None])[0]

    def set_human_triage(self, run_id: str, label: str, explanation: str) -> dict[str, Any]:
        """A reviewer's decision. It replaces the label and explanation; it never touches the
        run's pass/fail status."""
        row = self._conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] != "fail":
            raise ValueError("Only failed runs are triaged.")
        triage = Triage(label=label, explanation=explanation, confidence="high", engine="human")  # type: ignore[arg-type]
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET triage_label = ?, triage_explanation = ?, triage_json = ? "
                "WHERE id = ?",
                (triage.label.value, explanation, triage.model_dump_json(), run_id),
            )
        return triage.model_dump(mode="json")

    def batches(self, set_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where, args = ("WHERE b.requirement_set_id = ?", [set_id]) if set_id else ("", [])
        rows = self._conn.execute(
            f"""SELECT b.id, b.requirement_set_id, rs.source_name, b.started_at, b.finished_at,
                       b.base_url,
                       COUNT(r.id) AS total,
                       SUM(r.status = 'pass') AS passed,
                       SUM(r.status = 'fail') AS failed,
                       SUM(r.triage_label = 'real_regression') AS regressions,
                       SUM(r.triage_label = 'flaky') AS flaky,
                       SUM(r.triage_label = 'environment_issue') AS env_issues,
                       SUM(r.triage_label = 'test_defect') AS test_defects
                FROM run_batches b
                JOIN requirement_sets rs ON rs.id = b.requirement_set_id
                LEFT JOIN runs r ON r.batch_id = b.id
                {where}
                GROUP BY b.id ORDER BY b.started_at DESC, b.rowid DESC LIMIT ?""",  # noqa: S608 (where is a constant)
            [*args, limit],
        ).fetchall()
        return [{k: (r[k] or 0) if k in _COUNTS else r[k] for k in r.keys()} for r in rows]

    def batch_runs(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT r.*, tc.title, tc.requirement_ref, tc.priority, tc.seq
               FROM runs r JOIN test_cases tc ON tc.id = r.test_case_id
               WHERE r.batch_id = ? ORDER BY tc.seq""",
            (batch_id,),
        ).fetchall()
        previous = self._previous_batch_statuses(batch_id)
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d.pop("evidence_json"))
            d["triage"] = json.loads(d.pop("triage_json")) if d.get("triage_json") else None
            d["change"] = _change(previous.get(d["test_case_id"]), d["status"])
            out.append(d)
        return out

    def _previous_batch_statuses(self, batch_id: str) -> dict[str, str]:
        """Statuses from the batch before this one for the same requirement set."""
        row = self._conn.execute(
            """SELECT p.id FROM run_batches b JOIN run_batches p
                 ON p.requirement_set_id = b.requirement_set_id
                 AND (p.started_at < b.started_at OR (p.started_at = b.started_at
                      AND p.rowid < b.rowid))
               WHERE b.id = ? ORDER BY p.started_at DESC, p.rowid DESC LIMIT 1""",
            (batch_id,),
        ).fetchone()
        if not row:
            return {}
        return {
            r["test_case_id"]: r["status"]
            for r in self._conn.execute(
                "SELECT test_case_id, status FROM runs WHERE batch_id = ?", (row["id"],)
            )
        }

    def latest_runs_by_case(self, set_id: str) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT r.test_case_id, r.status, r.triage_label, r.run_at
               FROM runs r JOIN test_cases tc ON tc.id = r.test_case_id
               WHERE tc.requirement_set_id = ?
               ORDER BY r.run_at ASC, r.rowid ASC""",
            (set_id,),
        ).fetchall()
        return {r["test_case_id"]: dict(r) for r in rows}  # later rows overwrite earlier ones

    def flaky_rate(self, set_id: str | None = None) -> dict[str, Any]:
        where, args = ("WHERE tc.requirement_set_id = ?", [set_id]) if set_id else ("", [])
        r = self._conn.execute(
            f"""SELECT COUNT(*) AS runs, SUM(r.status='fail') AS failed,
                       SUM(r.triage_label='flaky') AS flaky
                FROM runs r JOIN test_cases tc ON tc.id = r.test_case_id {where}""",  # noqa: S608
            args,
        ).fetchone()
        runs, failed, flaky = r["runs"] or 0, r["failed"] or 0, r["flaky"] or 0
        return {
            "runs": runs,
            "failed": failed,
            "flaky": flaky,
            "flaky_rate": round(flaky / runs, 4) if runs else 0.0,
            "pass_rate": round((runs - failed) / runs, 4) if runs else 0.0,
        }


_COUNTS = {"total", "passed", "failed", "regressions", "flaky", "env_issues", "test_defects"}

# (table, column, declaration): applied by Store._migrate when the column is missing.
MIGRATIONS = [
    ("requirement_sets", "coverage_json", "TEXT"),  # traceability report from generation
    ("runs", "signature", "TEXT"),  # case + normalised first failure, for triage reuse
]


def _change(before: str | None, now_status: str) -> str:
    """Allure-style status change against the previous run of the same suite."""
    if before is None:
        return "first_run"
    if before == now_status:
        return "still_passing" if now_status == "pass" else "still_failing"
    return "fixed" if now_status == "pass" else "new_failure"
