"""Overlapping requests must not break the database layer.

FastAPI serves sync endpoints from a thread pool, so two visitors (or one dashboard switching
suites quickly) hit the store from different threads at the same time. Sharing one SQLite
connection across threads made such reads fail with sqlite3.InterfaceError (HTTP 500).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from test_pipeline import load_seed

from uatpilot.api import create_app
from uatpilot.config import Settings


def test_parallel_reads_all_succeed(tmp_path):
    client = TestClient(create_app(Settings(db_path=tmp_path / "t.db", engine="rules")))
    store = client.app.state.store
    ids = [load_seed(store), load_seed(store, "saucedemo")]
    paths = [
        p
        for set_id in ids
        for p in (
            f"/api/requirement-sets/{set_id}",
            f"/api/requirement-sets/{set_id}/matrix",
            f"/api/batches?requirement_set_id={set_id}",
        )
    ] + ["/api/requirement-sets", "/api/stats"]

    def hit(i: int) -> int:
        return client.get(paths[i % len(paths)]).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(hit, range(400)))
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}


def test_store_works_from_many_threads(tmp_path):
    from uatpilot.db import Store

    store = Store(tmp_path / "t.db")
    set_id = load_seed(store)

    def read(_: int) -> int:
        return len(store.test_cases(set_id)) + len(store.batches(set_id, 10))

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert len(set(pool.map(read, range(300)))) == 1
