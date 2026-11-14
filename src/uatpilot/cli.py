"""Command-line interface.

uatpilot generate examples/requirements/restful-booker.md --base-url https://restful-booker.herokuapp.com
uatpilot import examples/suites/restful-booker.seed.json
uatpilot run <requirement-set-id>
uatpilot export <requirement-set-id> --format md
uatpilot serve
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from uatpilot.config import get_settings, has_llm_credentials, load_dotenv
from uatpilot.db import Store
from uatpilot.export import to_csv, to_junit, to_markdown
from uatpilot.llm import AgentError
from uatpilot.models import TargetKind, TestSuite
from uatpilot.pipeline import run_suite
from uatpilot.requirements import coverage, extract_criteria
from uatpilot.target import TargetNotAllowed, check_target

app = typer.Typer(add_completion=False, help="UAT test cases from requirements, run for real.")


@app.callback()
def _startup() -> None:
    load_dotenv()  # a local .env (git-ignored) can hold the API key


console = Console()


def _store() -> Store:
    return Store(get_settings().db_path)


@app.command()
def generate(
    requirements: Annotated[Path, typer.Argument(exists=True, readable=True)],
    base_url: Annotated[str, typer.Option(help="Target base URL (must be allowlisted).")],
    kind: Annotated[TargetKind, typer.Option(help="api or ui")] = TargetKind.API,
    run: Annotated[bool, typer.Option(help="Run the suite right after generating it.")] = False,
    openapi: Annotated[
        str | None, typer.Option(help="OpenAPI/Swagger spec: a file path or an allowlisted URL.")
    ] = None,
) -> None:
    """Have the Claude agent write a UAT suite for a requirements doc."""
    from uatpilot.generator import Generator

    settings = get_settings()
    try:
        check_target(base_url, settings)
    except TargetNotAllowed as exc:
        raise typer.BadParameter(str(exc)) from exc
    spec_text = _load_spec(openapi, settings) if openapi else None
    if not has_llm_credentials(settings):
        console.print(
            "[red]Generation needs a model. Free: set GEMINI_API_KEY (or GROQ_API_KEY / "
            "OPENROUTER_API_KEY) in .env. Paid: ANTHROPIC_API_KEY.[/red]"
        )
        raise typer.Exit(2)
    gen = Generator(base_url=base_url, target_kind=kind, settings=settings)
    try:
        for ev in gen.generate(
            requirements.read_text(encoding="utf-8"), requirements.name, openapi=spec_text
        ):
            _print_event(ev)
    except AgentError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    assert gen.suite is not None
    store = _store()
    set_id = store.save_suite(
        source_name=requirements.name,
        raw_text=requirements.read_text(encoding="utf-8"),
        target_kind=kind.value,
        base_url=base_url,
        generated_by=settings.model_label,
        drafts=gen.suite.test_cases,
        coverage=gen.coverage,
    )
    console.print(f"[green]Saved {len(gen.suite.test_cases)} test cases as {set_id}[/green]")
    if run:
        _run(store, set_id)


def _load_spec(source: str, settings) -> str:
    """Read an OpenAPI spec from a file or an allowlisted URL and condense it for the agent."""
    import httpx

    from uatpilot.requirements import condense_openapi, parse_spec

    try:
        if source.startswith(("http://", "https://")):
            check_target(source, settings)
            text = httpx.get(source, timeout=settings.http_timeout).text
        else:
            text = Path(source).read_text(encoding="utf-8")
        return condense_openapi(parse_spec(text))
    except (TargetNotAllowed, OSError, ValueError, httpx.HTTPError) as exc:
        raise typer.BadParameter(f"--openapi: {exc}") from exc


@app.command("import")
def import_suite(path: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Import a hand-written suite JSON (see examples/suites). No model call."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    suite = TestSuite.model_validate({"test_cases": doc["test_cases"]})
    req_path = path.parent / doc["requirements"]
    raw = req_path.read_text(encoding="utf-8")
    report = coverage(extract_criteria(raw), suite)
    store = _store()
    set_id = store.save_suite(
        source_name=req_path.name,
        raw_text=raw,
        target_kind=doc["target_kind"],
        base_url=doc["base_url"],
        generated_by=f"import:{path.name}",
        drafts=suite.test_cases,
        coverage=report,
    )
    console.print(f"Imported {len(suite.test_cases)} test cases as [bold]{set_id}[/bold]")
    if report["missing"]:  # imports are not gated, but a gap is never silent
        console.print(f"[yellow]Not covered: {', '.join(report['missing'])}[/yellow]")


@app.command()
def seed(
    directory: Annotated[Path, typer.Option("--dir")] = Path("examples/suites"),
) -> None:
    """Import every *.seed.json in a folder, but only into an empty database (container start)."""
    if _store().requirement_sets():
        console.print("Database already has requirement sets; not seeding.")
        return
    for path in sorted(directory.glob("*.seed.json")):
        import_suite(path)


@app.command()
def run(set_id: str, base_url: Annotated[str | None, typer.Option()] = None) -> None:
    """Run every test case in a requirement set and triage the failures."""
    _run(_store(), set_id, base_url)


def _run(store: Store, set_id: str, base_url: str | None = None) -> None:
    table = Table("Case", "Result", "ms", "Triage")
    for ev in run_suite(store, set_id, get_settings(), base_url=base_url):
        if ev["type"] == "batch_start":
            console.print(
                f"Batch {ev['batch_id']}: {ev['total']} cases against {ev['base_url']} "
                f"(triage: {ev['triage_engine']})"
            )
        elif ev["type"] == "case_result":
            r = ev["run"]
            t = r.get("triage") or {}
            colour = "green" if r["status"] == "pass" else "red"
            table.add_row(
                ev["title"][:60],
                f"[{colour}]{r['status'].upper()}[/{colour}]",
                str(r["duration_ms"]),
                f"{t.get('label', '')}: {t.get('explanation', '')}"[:90] if t else "",
            )
        elif ev["type"] == "batch_done":
            console.print(table)
            console.print(f"[bold]{ev['pass']} passed, {ev['fail']} failed[/bold]")
        elif ev["type"] in ("error", "warning"):
            console.print(f"[yellow]{ev['message']}[/yellow]")


@app.command("list")
def list_sets() -> None:
    """List requirement sets."""
    table = Table("id", "source", "kind", "cases", "written by", "created")
    for rs in _store().requirement_sets():
        table.add_row(
            rs["id"],
            rs["source_name"],
            rs["target_kind"],
            str(rs["case_count"]),
            rs["generated_by"],
            rs["created_at"],
        )
    console.print(table)


@app.command()
def export(
    set_id: str,
    format: Annotated[str, typer.Option(help="md, csv or junit")] = "md",
    out: Annotated[Path | None, typer.Option(help="Write to a file instead of stdout.")] = None,
) -> None:
    """Export the UAT sign-off document (Markdown), a CSV, or JUnit XML for CI tools."""
    writers = {"md": to_markdown, "csv": to_csv, "junit": to_junit}
    if format not in writers:
        raise typer.BadParameter("format must be md, csv or junit")
    text = writers[format](_store(), set_id)
    if out:
        out.write_text(text, encoding="utf-8")
        console.print(f"Wrote {out}")
    else:
        print(text)


@app.command("eval")
def run_evaluation(
    set_id: str,
    out: Annotated[Path, typer.Option(help="Where to write the report.")] = Path("evals/results"),
    only: Annotated[str | None, typer.Option(help="Comma-separated mutation names.")] = None,
    triage_cases: Annotated[int, typer.Option(help="Cases per triage scenario.")] = 3,
) -> None:
    """Injected-bug detection rate + triage accuracy, through a local fault-injection proxy."""
    from uatpilot.evals.harness import run_eval, write_report
    from uatpilot.evals.mutations import CATALOGUE

    mutations = CATALOGUE
    if only:
        wanted = set(only.split(","))
        mutations = [m for m in CATALOGUE if m.name in wanted]
    report = run_eval(
        _store(),
        set_id,
        get_settings(),
        mutations=mutations,
        triage_cases=triage_cases,
        log=lambda s: console.print(s, markup=False),
    )
    jpath, mpath = write_report(report, out)
    d, t = report["detection"], report["triage"]
    console.print(
        f"[bold]Detection {d['detected']}/{d['measurable']} "
        f"({d['masked']} masked) · Triage {t['correct']}/{t['failures']}[/bold]"
    )
    console.print(f"Wrote {jpath} and {mpath}")


@app.command()
def doctor() -> None:
    """Check the configured model provider with one small tool call (costs one request)."""
    from uatpilot.llm import AgentError, Tool, ToolLoop, ToolOutcome

    settings = get_settings()
    key = settings.llm_api_key if settings.provider != "anthropic" else None
    console.print(f"Provider: {settings.provider}   Model: {settings.model}")
    if not has_llm_credentials(settings):
        console.print(
            "[red]No key found.[/red] Put one of GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY "
            "or ANTHROPIC_API_KEY in .env (see .env.example)."
        )
        raise typer.Exit(2)
    if key:
        console.print(f"Key: {key[:4]}...{key[-2:]} ({len(key)} characters)")
    loop = ToolLoop(
        settings=settings,
        system="You are a connectivity check. Call the report tool exactly once.",
        tools=[
            Tool(
                name="report",
                description="Report that tool calling works.",
                input_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    "required": ["status"],
                },
                handler=lambda args: ToolOutcome("Recorded.", accepted=args.get("status")),
            )
        ],
        submit_tool="report",
        nudge="Call the report tool now with status ok.",
        max_tokens=2000,
    )
    try:
        list(loop.run("Run the connectivity check."))
    except AgentError as exc:
        console.print(f"[red]Failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Working.[/green] {settings.model_label} answered with a tool call "
        f"({loop.usage['model_calls']} request(s))."
    )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the API + dashboard."""
    import uvicorn

    uvicorn.run("uatpilot.api:create_app", factory=True, host=host, port=port)


def _print_event(ev: dict) -> None:
    t = ev["type"]
    if t == "text":
        console.print(f"[dim]{ev['text'].strip()[:400]}[/dim]")
    elif t == "tool_call":
        console.print(f"[cyan]-> {ev['name']}[/cyan] {json.dumps(ev['input'])[:160]}")
    elif t == "tool_result":
        style = "red" if ev["is_error"] else "dim"
        console.print(f"[{style}]<- {ev['content'][:200]}[/{style}]")
    elif t == "submission":
        style = "green" if ev["accepted"] else "yellow"
        console.print(f"[{style}]submit: {ev['detail'][:600]}[/{style}]")
    elif t == "usage":
        console.print(f"[dim]usage: {ev['usage']}[/dim]")


if __name__ == "__main__":
    app()
