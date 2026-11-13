# UATPilot: Design

## Problem

A business team writes requirements in prose: user stories with acceptance criteria. Before each
release, someone turns them into structured UAT test cases, runs them, and reports pass/fail up
the chain. That is usually done by hand, in a spreadsheet, every release, and the sign-off
document is assembled manually at the end.

UATPilot automates that loop and keeps a person in charge of the decisions: the agent drafts and
triages, the code executes and keeps the record honest.

## Scope

1. **Input:** a requirements document (Markdown or plain text: user stories, acceptance criteria)
   and, optionally, the target's OpenAPI/Swagger spec.
2. **Generate:** a Claude tool-use agent drafts UAT test cases (preconditions, steps, expected
   result, priority, the criterion each case covers) through a strict Pydantic schema, so a
   hallucinated case can't silently corrupt the suite.
3. **Run:** each case carries an executable check. API cases make real HTTP calls and assert on
   real responses. Web UI cases drive a real browser through Playwright.
4. **Triage:** a failure is classified as a real regression, flaky (passed on an immediate
   re-run) or an environment issue (5xx, timeout, rate limit, auth expiry), with a one-line
   explanation. A failure is never left as a silent "failed".
5. **Report:** a dashboard shows the pass/fail trend, the flaky rate and the test cases, and
   exports a Markdown/CSV UAT sign-off document plus JUnit XML for CI systems.
6. **Safety floor:** the agent can label a failure but can never downgrade it to a pass. The
   label is metadata, and it is clamped to what the evidence supports.

### Later

- CI ingestion (a GitHub Action posting results into the dashboard).
- Data-quality mode: plain-English rules over a CSV/DuckDB table, through the same
  generate → run → triage → report flow.
- Notifications (Slack/Telegram) on new failures.

## Architecture

```
requirements.md (+ openapi.json) ─▶ generation agent ─strict schema─▶ test_cases
                                                                         │
                                                       runner: httpx | Playwright
                                                                         │ pass/fail + evidence
                                                                         ▼
                                          re-run ─▶ environment signals ─▶ triage agent (clamped)
                                                                         │
                                         runs + run_batches ─▶ FastAPI (SSE) ─▶ Next.js dashboard
```

Stack: Python, FastAPI, Pydantic, the Anthropic SDK (Claude tool use), httpx, Playwright, SQLite,
Next.js, Docker, GitHub Actions.

## Data model

| Table | Holds |
|---|---|
| `requirement_sets` | source document, target URL and kind, who wrote the cases |
| `test_cases` | title, criterion reference, preconditions, steps, expected result, priority, executable spec (JSON) |
| `run_batches` | one row per "run suite", for the trend over time |
| `runs` | one row per execution: status (never overwritten), duration, evidence, triage label, explanation and full triage record |

## Evaluation

Small and hand-checked, not a claimed benchmark (see `evals/README.md`):

1. **Detection rate:** labelled bugs are injected one at a time through a local proxy in front of
   the target; a bug counts as caught if a case that passes on the clean baseline now fails.
2. **Triage accuracy:** failures with a known correct label (flaky, environment, real).
3. **Generation quality:** the share of agent-written cases a QA reviewer accepts without edits
   (manual review).

The same person wrote the requirements, the bugs and the system, so the numbers are a smoke test,
not an independent benchmark.

## Milestones

| # | Scope |
|---|---|
| 1 | Schema + generation agent (CLI) |
| 2 | Runner (httpx + Playwright) + SQLite |
| 3 | Triage agent + safety floor |
| 4 | FastAPI SSE + Next.js dashboard, sign-off export |
| 5 | Eval set + README numbers |
| 6 | Docker, CI with a container smoke test, deploy |
