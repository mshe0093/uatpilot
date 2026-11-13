"""Talking to the system under test: host allowlist, a polite rate limit, and read-only probes the
generation agent uses to look at real responses before it writes assertions."""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from uatpilot.config import Settings


class TargetNotAllowed(ValueError):
    pass


class _RateLimiter:
    """At most one request per `interval` seconds, process-wide, per host."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, host: str, interval: float) -> None:
        if interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            ready_at = self._last.get(host, 0.0) + interval
            delay = max(0.0, ready_at - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)


RATE_LIMITER = _RateLimiter()


def check_target(url: str, settings: Settings) -> None:
    if not url.startswith(("http://", "https://")):
        raise TargetNotAllowed(f"{url!r} is not an http(s) URL")
    if not settings.host_allowed(url):
        raise TargetNotAllowed(
            f"{url!r} is not in UATPILOT_ALLOWED_HOSTS ({', '.join(settings.allowed_hosts)})"
        )


def join(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def make_client(settings: Settings, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    def _polite(request: httpx.Request) -> None:
        RATE_LIMITER.wait(request.url.host, settings.min_request_interval)

    return httpx.Client(
        timeout=settings.http_timeout,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=False,
        event_hooks={"request": [_polite]},
        transport=transport,
    )


def summarize_body(resp: httpx.Response, limit: int = 3000) -> Any:
    try:
        data = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.text[:limit]
    text = json.dumps(data)
    if len(text) <= limit:
        return data
    if isinstance(data, list):  # keep it valid JSON: show the first items and the length
        return {"_truncated_list": data[:5], "_length": len(data)}
    return text[:limit] + "...(truncated)"


def probe(
    base_url: str, path: str, settings: Settings, client: httpx.Client | None = None
) -> dict[str, Any]:
    """One read-only GET against the target. Used by the generation agent only."""
    url = join(base_url, path)
    check_target(url, settings)
    own = client is None
    client = client or make_client(settings)
    try:
        started = time.perf_counter()
        resp = client.get(url, headers={"Accept": "application/json"})
        return {
            "url": url,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "body": summarize_body(resp),
        }
    finally:
        if own:
            client.close()
