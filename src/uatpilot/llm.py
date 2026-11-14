"""A small hand-written tool-use loop shared by the generation and triage agents, with pluggable
model backends.

Same shape as scam-checker-agent's loop: every model turn, tool call and tool result is yielded as
a trace event (so the dashboard can stream it over SSE), and the run ends only when the model calls
the designated *submit* tool with input that passes the Pydantic schema. Invalid submissions go
back to the model as an error tool result with the validation messages, so it can fix them.

Backends:
  * AnthropicBackend: Claude through the Anthropic SDK (adaptive thinking, refusal fallback).
  * OpenAICompatBackend: any OpenAI-compatible chat-completions API with function calling, which
    covers the free tiers of Google Gemini, Groq and OpenRouter (see config.PROVIDERS).
The loop only sees a neutral `Turn`; each backend keeps its own message history in its own format.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from uatpilot.config import Settings

Event = dict[str, Any]
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    accepted: Any = None  # set by the submit tool when the submission is valid


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], ToolOutcome]


class AgentError(RuntimeError):
    pass


class RateLimited(AgentError):
    """The provider refused for quota; retrying within this batch will not help."""


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    parse_error: str | None = None  # the model sent arguments that aren't valid JSON


@dataclass
class Turn:
    texts: list[str] = field(default_factory=list)
    tool_uses: list[ToolUse] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class Backend(Protocol):
    label: str  # "claude:<model>" etc., recorded with what the agent produced

    def start(self, system: str, tools: list[Tool], user: str) -> None: ...
    def call(self) -> Turn: ...
    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None: ...
    def add_user_text(self, text: str) -> None: ...


# ---- Claude ------------------------------------------------------------------------------------
class AnthropicBackend:
    def __init__(self, client: anthropic.Anthropic, settings: Settings, max_tokens: int) -> None:
        self.client, self.settings, self.max_tokens = client, settings, max_tokens
        self.label = f"claude:{settings.model}"
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[Tool], user: str) -> None:
        self.system = system
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        self.messages = [{"role": "user", "content": user}]

    def call(self) -> Turn:
        try:
            # Streaming keeps long generations (a 20-case suite is a big tool input) clear of
            # HTTP timeouts; get_final_message() hands back the complete Message.
            with self.client.beta.messages.stream(
                model=self.settings.model,
                max_tokens=self.max_tokens,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise RateLimited("Claude API rate limit reached; try again later.") from exc
        except anthropic.APIStatusError as exc:
            raise AgentError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AgentError("Could not reach the Claude API.") from exc
        if response.stop_reason == "refusal":
            raise AgentError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise AgentError("The model ran out of output tokens before finishing.")
        self.messages.append({"role": "assistant", "content": response.content})
        return Turn(
            texts=[b.text for b in response.content if b.type == "text" and b.text.strip()],
            tool_uses=[
                ToolUse(b.id, b.name, dict(b.input))
                for b in response.content
                if b.type == "tool_use"
            ],
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": content,
                        **({"is_error": True} if err else {}),
                    }
                    for tid, content, err in results
                ],
            }
        )

    def add_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


# ---- OpenAI-compatible (Gemini, Groq, OpenRouter...) ------------------------------------------------
_DROP_KEYS = {"additionalProperties", "default", "discriminator", "$schema", "title", "examples"}


def portable_schema(node: Any) -> Any:
    """Reduce a JSON Schema to the subset every OpenAI-compatible provider accepts (Gemini is the
    strictest). Loosening the *tool* schema is safe: every submission is still validated by the
    strict Pydantic model on our side, and rejected with exact errors if it doesn't fit."""
    if isinstance(node, list):
        return [portable_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _DROP_KEYS:
            continue
        if k == "const":
            out["enum"] = [v]
        elif k == "oneOf":
            out["anyOf"] = portable_schema(v)
        elif k == "properties":
            out[k] = {name: portable_schema(sub) for name, sub in v.items()}
        else:
            out[k] = portable_schema(v)
    # A free-form object (e.g. a JSON body or a JSON Schema the agent writes) has no properties;
    # some providers reject an OBJECT without them, so describe it as a JSON-encoded string.
    if out.get("type") == "object" and not out.get("properties"):
        return {
            "type": "string",
            "description": (out.get("description", "") + " (JSON-encoded object)").strip(),
        }
    return out


def decode_json_strings(value: Any, schema: dict[str, Any]) -> Any:
    """Undo portable_schema's object->string trick: parse strings where the original schema
    allowed an object or array."""
    if not isinstance(schema, dict):
        return value
    if isinstance(value, str) and _allows(schema, ("object", "array")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: decode_json_strings(v, _pick(props.get(k, {}), v)) for k, v in value.items()}
    if isinstance(value, list):
        items = schema.get("items", {})
        return [decode_json_strings(v, _pick(items, v)) for v in value]
    return value


def _variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return schema.get("anyOf") or schema.get("oneOf") or [schema]


def _allows(schema: dict[str, Any], types: tuple[str, ...]) -> bool:
    return any(v.get("type") in types for v in _variants(schema)) and not any(
        v.get("type") == "string" for v in _variants(schema)
    )


def _pick(schema: dict[str, Any], value: Any) -> dict[str, Any]:
    """The anyOf/oneOf variant that matches the value's shape (by `kind` const when present)."""
    variants = _variants(schema)
    if len(variants) == 1:
        return variants[0]
    if isinstance(value, dict):
        for v in variants:
            kind = v.get("properties", {}).get("kind", {}).get("const")
            if kind is not None and value.get("kind") == kind:
                return v
        for v in variants:
            if v.get("type") == "object" or "properties" in v:
                return v
    if isinstance(value, list):
        return next((v for v in variants if v.get("type") == "array"), variants[0])
    return variants[0]


def _tool_call_dict(tc: Any) -> dict[str, Any]:
    if hasattr(tc, "model_dump"):
        return tc.model_dump(exclude_none=True)
    return {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }


def _error_detail(exc: Any) -> str:
    """The provider's own error message; the SDK's .message is often just 'Error code: 400'."""
    body = getattr(exc, "body", None)
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(exc.message)


class OpenAICompatBackend:
    def __init__(self, settings: Settings, max_tokens: int, client: Any = None) -> None:
        import openai

        self.settings, self.max_tokens = settings, max_tokens
        self.label = f"{settings.provider}:{settings.model}"
        self.client = client or openai.OpenAI(
            api_key=settings.llm_api_key, base_url=settings.llm_base_url, max_retries=1
        )
        self.messages: list[dict[str, Any]] = []
        self.schemas: dict[str, dict[str, Any]] = {}

    def start(self, system: str, tools: list[Tool], user: str) -> None:
        self.schemas = {t.name: t.input_schema for t in tools}
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": portable_schema(t.input_schema),
                },
            }
            for t in tools
        ]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def call(self) -> Turn:
        import openai

        try:
            response = self.client.chat.completions.create(
                model=self.settings.model,
                messages=self.messages,
                tools=self.tools,
                tool_choice="auto",
                max_tokens=self.max_tokens,
            )
        except openai.RateLimitError as exc:
            raise RateLimited(
                f"{self.settings.provider} free-tier rate limit reached; try again later."
            ) from exc
        except openai.APIStatusError as exc:
            raise AgentError(
                f"{self.settings.provider} API error {exc.status_code}: {_error_detail(exc)[:300]}"
            ) from exc
        except openai.APIConnectionError as exc:
            raise AgentError(f"Could not reach the {self.settings.provider} API.") from exc
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise AgentError("The model declined this request.")
        if choice.finish_reason == "length" and not choice.message.tool_calls:
            raise AgentError("The model ran out of output tokens before finishing.")
        msg = choice.message
        self.messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                **(
                    {
                        # model_dump keeps provider extras such as Gemini 3's thought
                        # signatures, which must be sent back on the next turn.
                        "tool_calls": [_tool_call_dict(tc) for tc in msg.tool_calls]
                    }
                    if msg.tool_calls
                    else {}
                ),
            }
        )
        uses = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
                args = decode_json_strings(args, self.schemas.get(tc.function.name, {}))
                uses.append(
                    ToolUse(tc.id, tc.function.name, args if isinstance(args, dict) else {})
                )
            except json.JSONDecodeError as exc:
                uses.append(ToolUse(tc.id, tc.function.name, {}, parse_error=str(exc)))
        usage = response.usage
        return Turn(
            texts=[msg.content] if msg.content and msg.content.strip() else [],
            tool_uses=uses,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        for tid, content, err in results:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": f"ERROR: {content}" if err else content,
                }
            )

    def add_user_text(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


def make_backend(settings: Settings, max_tokens: int, client: Any = None) -> Backend:
    """`client` is an injected SDK client (tests): an Anthropic-shaped one for the anthropic
    provider, an OpenAI-shaped one otherwise."""
    if settings.provider == "anthropic":
        return AnthropicBackend(client or anthropic.Anthropic(), settings, max_tokens)
    return OpenAICompatBackend(settings, max_tokens, client)


# ---- the loop ------------------------------------------------------------------------------------
class ToolLoop:
    def __init__(
        self,
        *,
        settings: Settings,
        system: str,
        tools: list[Tool],
        submit_tool: str,
        nudge: str,
        max_tokens: int = 32000,
        client: Any = None,
        backend: Backend | None = None,
    ) -> None:
        self.settings = settings
        self.system = system
        self.tools = {t.name: t for t in tools}
        self.submit_tool = submit_tool
        self.nudge = nudge
        self.backend = backend or make_backend(settings, max_tokens, client)
        self.result: Any = None
        self.usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 0, "tool_calls": 0}

    def run(self, user_content: str) -> Iterator[Event]:
        self.backend.start(self.system, list(self.tools.values()), user_content)
        nudged = False
        for _ in range(self.settings.max_agent_iterations):
            self.usage["model_calls"] += 1
            turn = self.backend.call()
            self.usage["input_tokens"] += turn.input_tokens
            self.usage["output_tokens"] += turn.output_tokens
            for text in turn.texts:
                yield {"type": "text", "text": text}

            if not turn.tool_uses:
                if nudged:
                    raise AgentError(f"The model stopped without calling {self.submit_tool}.")
                nudged = True
                self.backend.add_user_text(self.nudge)
                continue

            results: list[tuple[str, str, bool]] = []
            for b in turn.tool_uses:
                tool = self.tools.get(b.name)
                if b.name != self.submit_tool:
                    self.usage["tool_calls"] += 1
                    yield {"type": "tool_call", "id": b.id, "name": b.name, "input": b.input}
                if b.parse_error:
                    outcome = ToolOutcome(
                        f"Your arguments were not valid JSON ({b.parse_error}). Send them again.",
                        is_error=True,
                    )
                elif tool is None:
                    outcome = ToolOutcome(f"Unknown tool {b.name!r}.", is_error=True)
                else:
                    try:
                        outcome = tool.handler(dict(b.input))
                    except Exception as exc:  # a tool bug must not kill the run; tell the model
                        outcome = ToolOutcome(f"Tool failed: {exc}", is_error=True)
                if b.name == self.submit_tool:
                    yield {
                        "type": "submission",
                        "accepted": outcome.accepted is not None,
                        "detail": outcome.content if outcome.is_error else "accepted",
                    }
                    if outcome.accepted is not None:
                        self.result = outcome.accepted
                else:
                    yield {
                        "type": "tool_result",
                        "id": b.id,
                        "name": b.name,
                        "is_error": outcome.is_error,
                        "content": outcome.content[:2000],
                    }
                results.append((b.id, outcome.content, outcome.is_error))
            self.backend.add_tool_results(results)
            if self.result is not None:
                yield {"type": "usage", "usage": dict(self.usage), "model": self.backend.label}
                return
        raise AgentError("Stopped: too many agent iterations without a valid submission.")
