"""Free OpenAI-compatible providers (Gemini, Groq, OpenRouter): config, schema portability and the
full generation loop against a scripted OpenAI-style client."""

import json
from types import SimpleNamespace

import pytest

from uatpilot.config import Settings, has_llm_credentials, load_dotenv
from uatpilot.generator import Generator
from uatpilot.llm import decode_json_strings, portable_schema
from uatpilot.models import TargetKind, TestSuite, suite_json_schema
from uatpilot.triage import Signals, TriageAgent

BASE = "https://restful-booker.herokuapp.com"

CASE = {
    "title": "Ping returns success",
    "requirement_ref": "AC-1.1",
    "steps": ["Send GET /ping"],
    "expected_result": "A 2xx status.",
    "priority": "high",
    "executable": {
        "kind": "api",
        "steps": [
            {
                "name": "ping",
                "method": "GET",
                "path": "/ping",
                "json_body": {"a": 1},
                "assertions": [
                    {"source": "status_code", "op": "less_than", "expected": 300},
                    {
                        "source": "json",
                        "op": "matches_schema",
                        "json_schema": {"type": "object"},
                    },
                ],
            }
        ],
    },
}


# ---- a scripted stand-in for openai.OpenAI ------------------------------------------------------
def tool_call(name, args, id="call_1", raw=None):
    return SimpleNamespace(
        id=id,
        function=SimpleNamespace(
            name=name, arguments=raw if raw is not None else json.dumps(args)
        ),
    )


def completion(*calls, content="", finish="tool_calls"):
    msg = SimpleNamespace(content=content, tool_calls=list(calls) or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


class FakeOpenAI:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw)))
        return self.script.pop(0)


def gemini_settings(tmp_path, **kw):
    return Settings(provider="gemini", db_path=tmp_path / "t.db", engine="llm", **kw)


# ---- config ---------------------------------------------------------------------------------------
def test_provider_autodetect_and_defaults(monkeypatch, tmp_path):
    assert Settings().provider == "anthropic"  # nothing set
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = Settings()
    assert s.provider == "gemini" and s.model == "gemini-3.5-flash"
    assert s.llm_base_url.startswith("https://generativelanguage.googleapis.com")
    assert s.model_label == "gemini:gemini-3.5-flash" and has_llm_credentials(s)
    monkeypatch.setenv("UATPILOT_PROVIDER", "groq")
    assert Settings().provider == "groq" and not has_llm_credentials(Settings())
    monkeypatch.setenv("UATPILOT_PROVIDER", "nope")
    with pytest.raises(ValueError, match="UATPILOT_PROVIDER"):
        Settings()


def test_engine_claude_is_an_alias_for_llm():
    assert Settings(provider="anthropic", engine="claude").engine == "llm"


def test_dotenv_loads_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nGEMINI_API_KEY='from-file'\nexport UATPILOT_MODEL=x\nEMPTY=\n")
    monkeypatch.setenv("UATPILOT_MODEL", "already-set")
    load_dotenv(env)  # skipped: the test suite sets UATPILOT_NO_DOTENV
    import os

    assert "GEMINI_API_KEY" not in os.environ
    monkeypatch.delenv("UATPILOT_NO_DOTENV")
    load_dotenv(env)
    assert os.environ["GEMINI_API_KEY"] == "from-file"
    assert os.environ["UATPILOT_MODEL"] == "already-set"
    monkeypatch.delenv("GEMINI_API_KEY")


# ---- schema portability ---------------------------------------------------------------------------
def test_portable_schema_has_no_unsupported_keywords():
    text = json.dumps(portable_schema(suite_json_schema()))
    for keyword in (
        '"additionalProperties"',
        '"const"',
        '"oneOf"',
        '"default"',
        '"discriminator"',
    ):
        assert keyword not in text


def test_free_form_objects_round_trip_through_strings():
    schema = suite_json_schema()
    wire = json.loads(json.dumps(CASE))  # what the model would send, with objects as strings
    step = wire["executable"]["steps"][0]
    step["json_body"] = json.dumps(step["json_body"])
    step["assertions"][1]["json_schema"] = json.dumps(step["assertions"][1]["json_schema"])
    decoded = decode_json_strings({"test_cases": [wire]}, schema)
    suite = TestSuite.model_validate(decoded)
    a = suite.test_cases[0].executable.steps[0]
    assert a.json_body == {"a": 1} and a.assertions[1].json_schema == {"type": "object"}


# ---- the loop on an OpenAI-compatible backend ---------------------------------------------------------
def test_generation_on_free_provider(tmp_path):
    fake = FakeOpenAI(
        [
            completion(
                tool_call("submit_test_cases", {"test_cases": [{**CASE, "priority": "p0"}]})
            ),
            completion(tool_call("submit_test_cases", {"test_cases": [CASE]}, id="call_2")),
        ]
    )
    g = Generator(
        base_url=BASE, target_kind=TargetKind.API, client=fake, settings=gemini_settings(tmp_path)
    )
    events = list(g.generate("AC-1.1 ping works", "r.md"))
    assert [e["accepted"] for e in events if e["type"] == "submission"] == [False, True]
    assert events[-1]["model"] == "gemini:gemini-3.5-flash"
    first, second = fake.requests
    assert first["model"] == "gemini-3.5-flash"
    assert first["messages"][0]["role"] == "system"
    assert first["tools"][-1]["function"]["name"] == "submit_test_cases"
    # the rejection went back as a tool message tied to the call id
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["content"].startswith("ERROR:") and "priority" in tool_msg["content"]


def test_invalid_json_arguments_are_bounced(tmp_path):
    fake = FakeOpenAI(
        [
            completion(tool_call("submit_test_cases", None, raw="{not json")),
            completion(tool_call("submit_test_cases", {"test_cases": [CASE]}, id="call_2")),
        ]
    )
    g = Generator(
        base_url=BASE, target_kind=TargetKind.API, client=fake, settings=gemini_settings(tmp_path)
    )
    events = list(g.generate("AC-1.1 ping", "r.md"))
    subs = [e for e in events if e["type"] == "submission"]
    assert subs[0]["accepted"] is False and "not valid JSON" in subs[0]["detail"]
    assert g.suite is not None


def test_triage_records_the_model(tmp_path):
    from test_triage import CASE as TRIAGE_CASE
    from test_triage import step

    fake = FakeOpenAI(
        [
            completion(
                tool_call(
                    "submit_triage",
                    {
                        "label": "real_regression",
                        "explanation": "Expected 200 from ping, got 404.",
                        "confidence": "high",
                    },
                )
            )
        ]
    )
    agent = TriageAgent(base_url=BASE, client=fake, settings=gemini_settings(tmp_path))
    events = list(
        agent.triage(TRIAGE_CASE, {"steps": [step(404)]}, {"passed": False}, Signals(False))
    )
    t = events[-1]["triage"]
    assert t.engine == "llm" and t.model == "gemini:gemini-3.5-flash"


def test_rate_limit_becomes_agent_error(tmp_path):
    import httpx
    import openai

    from uatpilot.llm import AgentError

    class Limited(FakeOpenAI):
        def _create(self, **kw):
            raise openai.RateLimitError(
                "slow down",
                response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                body=None,
            )

    g = Generator(
        base_url=BASE,
        target_kind=TargetKind.API,
        client=Limited([]),
        settings=gemini_settings(tmp_path),
    )
    with pytest.raises(AgentError, match="rate limit"):
        list(g.generate("AC-1.1 ping", "r.md"))


def test_doctor_reports_missing_key(tmp_path):
    from typer.testing import CliRunner

    from uatpilot.cli import app

    r = CliRunner().invoke(app, ["doctor"], env={"UATPILOT_PROVIDER": "gemini"})
    assert r.exit_code == 2 and "No key found" in r.output


def test_doctor_succeeds_on_a_tool_call(monkeypatch):
    from typer.testing import CliRunner

    from uatpilot import llm
    from uatpilot.cli import app

    fake = FakeOpenAI([completion(tool_call("report", {"status": "ok"}))])
    real = llm.make_backend
    monkeypatch.setattr(llm, "make_backend", lambda s, m, client=None: real(s, m, fake))
    r = CliRunner().invoke(
        app, ["doctor"], env={"UATPILOT_PROVIDER": "gemini", "GEMINI_API_KEY": "AIzaTESTKEY0123"}
    )
    assert r.exit_code == 0, r.output
    assert "Working" in r.output and "AIza...23" in r.output and "TESTKEY" not in r.output


def test_provider_extras_on_tool_calls_are_sent_back(tmp_path):
    """Gemini 3 rejects a follow-up turn (400) unless each tool call's thought signature returns."""
    from openai.types.chat import ChatCompletionMessageToolCall

    signed = ChatCompletionMessageToolCall.model_validate(
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "submit_test_cases", "arguments": "{not json"},
            "extra_content": {"google": {"thought_signature": "sig-abc"}},
        }
    )
    fake = FakeOpenAI(
        [
            completion(signed),
            completion(tool_call("submit_test_cases", {"test_cases": [CASE]}, id="call_2")),
        ]
    )
    g = Generator(
        base_url=BASE, target_kind=TargetKind.API, client=fake, settings=gemini_settings(tmp_path)
    )
    list(g.generate("AC-1.1 ping", "r.md"))
    echoed = next(m for m in fake.requests[1]["messages"] if m["role"] == "assistant")
    assert echoed["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig-abc"}}


def test_quota_error_switches_the_batch_to_rules(tmp_path):
    """After one 429 the rest of the run is triaged by rules, without calling the model again."""
    import httpx
    import openai
    from test_triage import CASE as TRIAGE_CASE
    from test_triage import step

    from uatpilot.triage import triage_or_fallback

    calls = []

    class Limited(FakeOpenAI):
        def _create(self, **kw):
            calls.append(kw)
            raise openai.RateLimitError(
                "quota",
                response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                body=None,
            )

    agent = TriageAgent(base_url=BASE, client=Limited([]), settings=gemini_settings(tmp_path))
    agent._health_checked = True  # skip the live health probe
    evidence, rerun = {"steps": [step(404)]}, {"passed": False}
    first = list(triage_or_fallback(agent, TRIAGE_CASE, evidence, rerun))
    second = list(triage_or_fallback(agent, TRIAGE_CASE, evidence, rerun))
    assert any(e["type"] == "warning" for e in first)
    assert not any(e["type"] == "warning" for e in second)
    assert second[-1]["triage"].engine == "rules" and len(calls) == 1
