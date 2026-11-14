"""Runtime configuration from environment variables (and an optional local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# Public sandboxes that exist for people to practise test automation against. The runner refuses
# every other host unless it is added here, so a deployed instance can't be pointed at arbitrary
# infrastructure (SSRF) and the tool can't be used to hammer a third party.
DEFAULT_ALLOWED_HOSTS = (
    "restful-booker.herokuapp.com",  # API sandbox built for API-testing practice
    "www.saucedemo.com",  # UI sandbox built for UI-automation practice
    "localhost",
    "127.0.0.1",
)


@dataclass(frozen=True)
class Provider:
    key_env: str  # where the API key comes from
    base_url: str | None  # OpenAI-compatible endpoint (None = Anthropic SDK)
    default_model: str
    free_tier: bool


# Anthropic is the reference backend. The others are OpenAI-compatible APIs with a free tier
# that supports function calling, so the agents can run without paying for anything.
PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("ANTHROPIC_API_KEY", None, "claude-opus-5-5", free_tier=False),
    "gemini": Provider(
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-3.5-flash",
        free_tier=True,
    ),
    "groq": Provider(
        "GROQ_API_KEY", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", free_tier=True
    ),
    "openrouter": Provider(
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
        free_tier=True,
    ),
    # GitHub Models: free and rate limited for any GitHub account. The key is a GitHub token;
    # when GITHUB_TOKEN is unset, the GitHub CLI's login is used (see github_cli_token).
    "github": Provider(
        "GITHUB_TOKEN", "https://models.github.ai/inference", "openai/gpt-4.1", free_tier=True
    ),
}


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def _provider() -> str:
    """UATPILOT_PROVIDER if set; otherwise the first provider whose API key is present."""
    explicit = os.getenv("UATPILOT_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in PROVIDERS:
            raise ValueError(f"UATPILOT_PROVIDER must be one of {', '.join(PROVIDERS)}")
        return explicit
    if os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    return next((name for name, p in PROVIDERS.items() if os.getenv(p.key_env)), "anthropic")


@dataclass(frozen=True)
class Settings:
    provider: str = field(default_factory=_provider)
    model: str = ""  # empty: the provider's default (see __post_init__)
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
    # "auto" uses the model when credentials exist and the rules fallback otherwise; "rules"
    # never calls a model (CI, offline demos); "llm" fails loudly when the model is unreachable.
    engine: str = field(default_factory=lambda: os.getenv("UATPILOT_ENGINE", "auto"))
    write_token: str | None = field(
        default_factory=lambda: os.getenv("UATPILOT_WRITE_TOKEN") or None
    )
    user_agent: str = "UATPilot/0.2 (test-automation portfolio project)"

    def __post_init__(self) -> None:
        if not self.model:
            model = os.getenv("UATPILOT_MODEL") or PROVIDERS[self.provider].default_model
            object.__setattr__(self, "model", model)
        if self.engine == "claude":  # pre-0.3 name for "llm"
            object.__setattr__(self, "engine", "llm")

    @property
    def llm_api_key(self) -> str | None:
        key = os.getenv(PROVIDERS[self.provider].key_env) or None
        if key is None and self.provider == "github":
            key = github_cli_token()
        return key

    @property
    def llm_base_url(self) -> str | None:
        return os.getenv("UATPILOT_LLM_BASE_URL") or PROVIDERS[self.provider].base_url

    @property
    def model_label(self) -> str:
        return f"{'claude' if self.provider == 'anthropic' else self.provider}:{self.model}"

    def host_allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in self.allowed_hosts


def github_cli_token() -> str | None:
    """The token of the GitHub CLI's current login, if `gh` is installed and signed in."""
    import shutil
    import subprocess

    gh = shutil.which("gh")
    if gh is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            [gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


def get_settings() -> Settings:
    return Settings()


def has_llm_credentials(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if s.provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    return bool(s.llm_api_key)


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from a local .env into the environment, without overriding variables
    that are already set. Called by the CLI and the API at start-up only (never on import), and
    skipped when UATPILOT_NO_DOTENV is set (the test suite sets it)."""
    if os.getenv("UATPILOT_NO_DOTENV") or not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
