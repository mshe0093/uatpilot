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

from uatpilot.models import RunResult, TestCase, TestCaseDraft

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
    ) -> str:
        set_id = new_id("rs")
        with self.tx() as c:
            c.execute(
                "INSERT INTO requirement_sets VALUES (?,?,?,?,?,?,?)",
                (set_id, source_name, raw_text, target_kind, base_url, generated_by, now()),
            )
            for i, d in enumerate(drafts, start=1):
                c.execute(
                    "INSERT INTO test_cases VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        return dict(row) if row else None

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
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )

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
                       SUM(r.triage_label = 'environment_issue') AS env_issues
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
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d.pop("evidence_json"))
            d["triage"] = json.loads(d.pop("triage_json")) if d.get("triage_json") else None
            out.append(d)
        return out

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


_COUNTS = {"total", "passed", "failed", "regressions", "flaky", "env_issues"}
