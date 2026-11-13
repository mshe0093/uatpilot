from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from uatpilot.config import Settings
from uatpilot.db import Store

BASE = "https://restful-booker.herokuapp.com"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Tests never read a developer's .env or real API keys."""
    monkeypatch.setenv("UATPILOT_NO_DOTENV", "1")
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "GITHUB_TOKEN",
        "UATPILOT_PROVIDER",
        "UATPILOT_MODEL",
        "UATPILOT_LLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        provider="anthropic",
        db_path=tmp_path / "t.db",
        artifacts_dir=tmp_path / "artifacts",
        min_request_interval=0.0,
        engine="rules",
    )


@pytest.fixture
def store(settings) -> Store:
    s = Store(settings.db_path)
    yield s
    s.close()


# ---- a scripted stand-in for anthropic.Anthropic ----------------------------------------------
def tool_use(name: str, input: dict[str, Any], id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def message(*blocks: SimpleNamespace, stop_reason: str | None = None) -> SimpleNamespace:
    has_tool = any(b.type == "tool_use" for b in blocks)
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason or ("tool_use" if has_tool else "end_turn"),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


@dataclass
class FakeClient:
    """Returns the scripted messages in order and records every request."""

    script: list[SimpleNamespace]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        outer = self

        class _Stream:
            def __init__(self, msg):
                self.msg = msg

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return self.msg

        class _Messages:
            def stream(self, **kwargs):
                outer.requests.append(json.loads(json.dumps(kwargs, default=str)))
                return _Stream(outer.script.pop(0))

        self.beta = SimpleNamespace(messages=_Messages())
