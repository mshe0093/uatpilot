"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_MODEL = "claude-opus-5-5"

# Public sandboxes that exist for people to practise test automation against. The runner refuses
# every other host unless it is added here, so a deployed instance can't be pointed at arbitrary
# infrastructure (SSRF) and the tool can't be used to hammer a third party.
DEFAULT_ALLOWED_HOSTS = (
    "restful-booker.herokuapp.com",  # API sandbox built for API-testing practice
    "www.saucedemo.com",  # UI sandbox built for UI-automation practice
    "localhost",
    "127.0.0.1",
)


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


@dataclass(frozen=True)
class Settings:
    model: str = field(default_factory=lambda: os.getenv("UATPILOT_MODEL", DEFAULT_MODEL))
    effort: str = field(default_factory=lambda: os.getenv("UATPILOT_EFFORT", "medium"))
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("UATPILOT_DB", "data/uatpilot.db"))
    )
    artifacts_dir: Path = field(
        default_factory=lambda: Path(os.getenv("UATPILOT_ARTIFACTS", "data/artifacts"))
    )
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: _csv("UATPILOT_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
    )
    # Politeness toward public sandboxes: minimum gap between two requests, in seconds.
    min_request_interval: float = field(
        default_factory=lambda: float(os.getenv("UATPILOT_MIN_REQUEST_INTERVAL", "0.3"))
    )
    http_timeout: float = 10.0
    ui_timeout_ms: int = 8000
    max_agent_iterations: int = 14
    max_probes: int = 8
    # "auto" uses Claude when credentials exist and the rules fallback otherwise; "rules" never
    # calls the model (CI, offline demos); "claude" fails loudly when the model is unreachable.
    engine: str = field(default_factory=lambda: os.getenv("UATPILOT_ENGINE", "auto"))
    write_token: str | None = field(
        default_factory=lambda: os.getenv("UATPILOT_WRITE_TOKEN") or None
    )
    user_agent: str = "UATPilot/0.1 (test-automation portfolio project)"

    def host_allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in self.allowed_hosts


def get_settings() -> Settings:
    return Settings()


def has_claude_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
