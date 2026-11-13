"""A small hand-written Claude tool-use loop shared by the generation and triage agents.

Same shape as scam-checker-agent's loop: every model turn, tool call and tool result is yielded as
a trace event (so the dashboard can stream it over SSE), and the run ends only when the model calls
the designated *submit* tool with input that passes the Pydantic schema. Invalid submissions go
back to the model as an error tool_result with the validation messages, so it can fix them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

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

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class AgentError(RuntimeError):
    pass


class ToolLoop:
    def __init__(
        self,
        *,
        client: anthropic.Anthropic,
        settings: Settings,
        system: str,
        tools: list[Tool],
        submit_tool: str,
        nudge: str,
        max_tokens: int = 32000,
    ) -> None:
        self.client = client
        self.settings = settings
        self.system = system
        self.tools = {t.name: t for t in tools}
        self.submit_tool = submit_tool
        self.nudge = nudge
        self.max_tokens = max_tokens
        self.result: Any = None
        self.usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 0, "tool_calls": 0}

    def _call(self, messages: list[dict[str, Any]]) -> Any:
        self.usage["model_calls"] += 1
        # Streaming keeps long generations (a 20-case suite is a big tool input) clear of HTTP
        # timeouts; get_final_message() hands back the complete Message.
        with self.client.beta.messages.stream(
            model=self.settings.model,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=[t.definition() for t in self.tools.values()],
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            return stream.get_final_message()

    def run(self, user_content: str) -> Iterator[Event]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        nudged = False
        for _ in range(self.settings.max_agent_iterations):
            try:
                response = self._call(messages)
            except anthropic.APIStatusError as exc:
                raise AgentError(f"Claude API error {exc.status_code}: {exc.message}") from exc
            except anthropic.APIConnectionError as exc:
                raise AgentError("Could not reach the Claude API.") from exc
            self.usage["input_tokens"] += response.usage.input_tokens
            self.usage["output_tokens"] += response.usage.output_tokens

            if response.stop_reason == "refusal":
                raise AgentError("The model declined this request.")
            if response.stop_reason == "max_tokens":
                raise AgentError("The model ran out of output tokens before finishing.")
            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    yield {"type": "text", "text": block.text}

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if nudged:
                    raise AgentError(f"The model stopped without calling {self.submit_tool}.")
                nudged = True
                messages.append({"role": "user", "content": self.nudge})
                continue

            results: list[dict[str, Any]] = []
            for b in tool_uses:
                tool = self.tools.get(b.name)
                if b.name != self.submit_tool:
                    self.usage["tool_calls"] += 1
                    yield {"type": "tool_call", "id": b.id, "name": b.name, "input": b.input}
                if tool is None:
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
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": outcome.content,
                        **({"is_error": True} if outcome.is_error else {}),
                    }
                )
            messages.append({"role": "user", "content": results})
            if self.result is not None:
                yield {"type": "usage", "usage": dict(self.usage)}
                return
        raise AgentError("Stopped: too many agent iterations without a valid submission.")
