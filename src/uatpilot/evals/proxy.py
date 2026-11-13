"""A local fault-injection proxy in front of the public sandbox.

The eval needs bugs whose location is known in advance, but the target is a public service we
don't control. So the eval points the runner at this proxy (127.0.0.1), which forwards every
request to the real sandbox and, when a mutation is active, corrupts the request handling in one
specific, labelled way. Nothing is changed on the sandbox itself.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from uatpilot.config import Settings
from uatpilot.target import RATE_LIMITER


@dataclass
class Exchange:
    method: str
    path: str  # e.g. /booking/123
    query: dict[str, str]
    headers: dict[str, str]
    body: Any  # parsed JSON or None


@dataclass
class Reply:
    status: int
    body: Any  # JSON-able, or str
    headers: dict[str, str] = field(default_factory=dict)

    def to_response(self) -> Response:
        if isinstance(self.body, dict | list):
            return Response(json.dumps(self.body), self.status, self.headers, "application/json")
        return Response(str(self.body or ""), self.status, self.headers, "text/plain")


Pre = Callable[[Exchange], Reply | None]  # short-circuit before forwarding
Post = Callable[[Exchange, Reply], Reply]  # rewrite the upstream reply


@dataclass
class Mode:
    pre: Pre | None = None
    post: Post | None = None
    delay_s: float = 0.0
    budget: int | None = None  # flaky modes: only misbehave this many times, then pass through


class FaultProxy:
    def __init__(self, upstream: str, settings: Settings) -> None:
        self.upstream = upstream.rstrip("/")
        self.settings = settings
        self.mode: Mode | None = None
        self.lock = threading.Lock()
        self.client = httpx.Client(
            timeout=30.0, headers={"User-Agent": settings.user_agent}, follow_redirects=False
        )
        self.server: uvicorn.Server | None = None
        self.port = 0

    def set_mode(self, mode: Mode | None) -> None:
        with self.lock:
            self.mode = mode

    def _take(self) -> Mode | None:
        with self.lock:
            m = self.mode
            if m is None:
                return None
            if m.budget is not None:
                if m.budget <= 0:
                    return None
                m.budget -= 1
            return m

    async def _handle(self, request: Request) -> Response:
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        ex = Exchange(
            method=request.method,
            path=request.url.path,
            query=dict(request.query_params),
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
        )
        # Forwarding is blocking httpx; run it off the event loop.
        from starlette.concurrency import run_in_threadpool

        reply = await run_in_threadpool(self._process, ex, raw)
        return reply.to_response()

    def _process(self, ex: Exchange, raw: bytes) -> Reply:
        mode = self._take()
        if mode and mode.delay_s:
            time.sleep(mode.delay_s)
        if mode and mode.pre and (short := mode.pre(ex)) is not None:
            return short
        reply = self._forward(ex, raw)
        if mode and mode.post:
            reply = mode.post(ex, reply)
        return reply

    def _forward(self, ex: Exchange, raw: bytes) -> Reply:
        RATE_LIMITER.wait(httpx.URL(self.upstream).host, self.settings.min_request_interval)
        fwd_headers = {
            k: v
            for k, v in ex.headers.items()
            if k in ("content-type", "accept", "cookie", "authorization")
        }
        r = self.client.request(
            ex.method,
            self.upstream + ex.path,
            params=ex.query or None,
            headers=fwd_headers,
            content=raw or None,
        )
        try:
            body: Any = r.json()
        except ValueError:
            body = r.text
        return Reply(r.status_code, body)

    # ---- lifecycle ------------------------------------------------------------------------
    def start(self) -> str:
        app = Starlette(
            routes=[
                Route(
                    "/{path:path}",
                    self._handle,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                )
            ]
        )
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        thread = threading.Thread(target=self.server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if self.server.started and self.server.servers:
                break
            time.sleep(0.05)
        sock = self.server.servers[0].sockets[0]
        self.port = sock.getsockname()[1]
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        self.client.close()
