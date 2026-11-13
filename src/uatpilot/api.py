"""FastAPI backend for the dashboard: read endpoints, SSE streams for generate/run, exports.

`uvicorn uatpilot.api:create_app --factory`
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from uatpilot import __version__
from uatpilot.config import Settings, get_settings, has_llm_credentials, load_dotenv
from uatpilot.db import Store
from uatpilot.export import to_csv, to_junit, to_markdown, traceability
from uatpilot.llm import AgentError
from uatpilot.models import TargetKind, TriageLabel
from uatpilot.pipeline import run_suite, use_llm
from uatpilot.requirements import condense_openapi, parse_spec
from uatpilot.target import TargetNotAllowed, check_target


class GenerateRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=120)
    requirements: str = Field(min_length=20, max_length=40_000)
    base_url: str = Field(max_length=300)
    target_kind: TargetKind
    # The spec's text (JSON or YAML), not a URL: fetching a URL server-side would be an SSRF hole.
    openapi: str | None = Field(default=None, max_length=2_000_000)


class HumanTriageRequest(BaseModel):
    label: TriageLabel
    explanation: str = Field(min_length=10, max_length=400)


class RunRequest(BaseModel):
    requirement_set_id: str = Field(pattern=r"^rs_[0-9a-f]{12}$")
    case_ids: list[str] | None = None


def sse(event: dict[str, Any]) -> str:
    return (
        f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, default=_jsonable)}\n\n"
    )


def _jsonable(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    return str(o)


class _Limiter:
    """Sliding-window limit on write calls per client IP (generation costs real money)."""

    def __init__(self, limit: int, window_s: int) -> None:
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        load_dotenv()
        settings = get_settings()
    store = Store(settings.db_path)
    busy = threading.Lock()  # one suite run at a time: be polite to the public sandboxes
    limiter = _Limiter(limit=int(os.getenv("UATPILOT_WRITE_LIMIT_PER_HOUR", "20")), window_s=3600)
    app = FastAPI(title="UATPilot", version=__version__)
    app.state.store = store

    dev_origins = ",".join(
        f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (3000, 3001, 3005)
    )
    origins = [o for o in os.getenv("UATPILOT_CORS_ORIGINS", dev_origins).split(",") if o]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    def guard_write(request: Request) -> None:
        if settings.write_token:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {settings.write_token}":
                raise HTTPException(401, "This deployment is read-only without a write token.")
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            raise HTTPException(429, "Too many runs from this address; try again later.")

    # ---- read endpoints ------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "llm_available": use_llm(settings),
            "model": settings.model_label if use_llm(settings) else None,
            "generation_available": settings.engine != "rules" and has_llm_credentials(settings),
            "read_only": settings.write_token is not None,
            "allowed_hosts": list(settings.allowed_hosts),
        }

    @app.get("/api/requirement-sets")
    def list_sets() -> list[dict[str, Any]]:
        return store.requirement_sets()

    @app.get("/api/requirement-sets/{set_id}")
    def get_set(set_id: str) -> dict[str, Any]:
        rs = store.requirement_set(set_id)
        if rs is None:
            raise HTTPException(404, "No such requirement set")
        latest = store.latest_runs_by_case(set_id)
        cases = []
        for c in store.test_cases(set_id):
            d = c.model_dump(mode="json")
            d["latest_run"] = latest.get(c.id)
            cases.append(d)
        return {
            **rs,
            "test_cases": cases,
            "stats": store.flaky_rate(set_id),
            "traceability": traceability(store, set_id),
        }

    @app.get("/api/requirement-sets/{set_id}/matrix")
    def run_matrix(set_id: str, limit: int = 24) -> dict[str, Any]:
        if store.requirement_set(set_id) is None:
            raise HTTPException(404, "No such requirement set")
        return store.run_matrix(set_id, min(max(limit, 1), 100))

    @app.get("/api/batches")
    def list_batches(requirement_set_id: str | None = None, limit: int = 50) -> list[dict]:
        return list(reversed(store.batches(requirement_set_id, min(limit, 200))))

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict[str, Any]:
        runs = store.batch_runs(batch_id)
        if not runs:
            raise HTTPException(404, "No such batch (or it has no runs yet)")
        return {"batch_id": batch_id, "runs": runs}

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        sets = store.requirement_sets()
        return {
            "overall": store.flaky_rate(),
            "requirement_sets": len(sets),
            "test_cases": sum(s["case_count"] for s in sets),
        }

    @app.get("/api/requirement-sets/{set_id}/export.md", response_class=PlainTextResponse)
    def export_md(set_id: str) -> PlainTextResponse:
        try:
            text = to_markdown(store, set_id)
        except KeyError as exc:
            raise HTTPException(404, "No such requirement set") from exc
        return PlainTextResponse(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="uat-signoff-{set_id}.md"'},
        )

    @app.get("/api/requirement-sets/{set_id}/export.csv", response_class=PlainTextResponse)
    def export_csv(set_id: str) -> PlainTextResponse:
        if store.requirement_set(set_id) is None:
            raise HTTPException(404, "No such requirement set")
        return PlainTextResponse(
            to_csv(store, set_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="uat-{set_id}.csv"'},
        )

    @app.get("/api/requirement-sets/{set_id}/junit.xml")
    def export_junit(set_id: str) -> PlainTextResponse:
        try:
            xml = to_junit(store, set_id)
        except KeyError as exc:
            raise HTTPException(404, "No such requirement set") from exc
        return PlainTextResponse(
            xml,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="uat-{set_id}.junit.xml"'},
        )

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "No such run")
        return run

    @app.put("/api/runs/{run_id}/triage")
    def human_triage(run_id: str, body: HumanTriageRequest, request: Request) -> dict[str, Any]:
        """A reviewer's label for a failed run. It never changes the run's pass/fail status, and
        later identical failures of the same case reuse it before asking the model."""
        guard_write(request)
        try:
            return store.set_human_triage(run_id, body.label.value, body.explanation)
        except KeyError as exc:
            raise HTTPException(404, "No such run") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/artifacts/{name}")
    def artifact(name: str) -> FileResponse:
        if not re.fullmatch(r"run_[0-9a-f]{12}(_rerun)?\.png", name):
            raise HTTPException(404)
        path = Path(settings.artifacts_dir) / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    # ---- streaming write endpoints -------------------------------------------------------
    def _stream(events: Iterator[dict[str, Any]]) -> Iterator[str]:
        try:
            for ev in events:
                yield sse(ev)
        except AgentError as exc:
            yield sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # never leak a traceback into the stream
            yield sse({"type": "error", "message": f"Internal error ({type(exc).__name__})."})

    @app.post("/api/run")
    def run(body: RunRequest, request: Request) -> StreamingResponse:
        guard_write(request)
        if store.requirement_set(body.requirement_set_id) is None:
            raise HTTPException(404, "No such requirement set")

        def events() -> Iterator[dict[str, Any]]:
            if not busy.acquire(blocking=False):
                yield {"type": "error", "message": "Another suite is running; try again shortly."}
                return
            try:
                yield from run_suite(
                    store, body.requirement_set_id, settings, case_ids=body.case_ids
                )
            finally:
                busy.release()

        return StreamingResponse(
            _stream(events()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/generate")
    def generate(body: GenerateRequest, request: Request) -> StreamingResponse:
        guard_write(request)
        if settings.engine == "rules" or not has_llm_credentials(settings):
            raise HTTPException(
                503,
                "Generation needs a model: set GEMINI_API_KEY (free), GROQ_API_KEY, "
                "OPENROUTER_API_KEY or ANTHROPIC_API_KEY on the server.",
            )
        try:
            check_target(body.base_url, settings)
        except TargetNotAllowed as exc:
            raise HTTPException(400, str(exc)) from exc
        openapi = None
        if body.openapi:
            try:
                openapi = condense_openapi(parse_spec(body.openapi))
            except Exception as exc:  # YAML/JSON errors from user input
                raise HTTPException(400, f"Could not read the OpenAPI spec: {exc}") from exc
        from uatpilot.generator import Generator

        def events() -> Iterator[dict[str, Any]]:
            gen = Generator(
                base_url=body.base_url, target_kind=body.target_kind, settings=settings
            )
            yield from gen.generate(body.requirements, body.source_name, openapi=openapi)
            assert gen.suite is not None
            set_id = store.save_suite(
                source_name=body.source_name,
                raw_text=body.requirements,
                target_kind=body.target_kind.value,
                base_url=body.base_url,
                generated_by=settings.model_label,
                drafts=gen.suite.test_cases,
                coverage=gen.coverage,
            )
            yield {
                "type": "saved",
                "requirement_set_id": set_id,
                "case_count": len(gen.suite.test_cases),
            }

        return StreamingResponse(
            _stream(events()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Same-origin static dashboard in production (see Dockerfile).
    static_dir = os.getenv("UATPILOT_STATIC_DIR")
    if static_dir and os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app
