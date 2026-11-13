# UATPilot

**Requirements in, UAT test cases out: an agent writes the cases, they run for real, every
failure is triaged, and the results land on a dashboard.**

Someone (often non-technical) writes requirements in prose. A junior analyst turns them into
structured UAT test cases, runs them, and reports pass/fail up the chain, by hand, in a
spreadsheet, every release. UATPilot automates that loop:

1. **Generate.** A Claude tool-use agent reads a requirements doc, *looks at the real target
   first* (read-only `http_probe` for APIs; `inspect_page` in a real browser for web UIs), and
   writes test cases through a strict Pydantic schema. Any invalid case (a hallucinated field, an
   unknown operator, a step with no assertion) is bounced back to the model with the exact
   validation errors. It is never stored.
2. **Run.** Each case is executable: API cases make real HTTP calls with `httpx` and assert on the
   real responses, chaining ids between steps (`{{booking_id}}`). UI cases drive headless Chromium
   through Playwright and take a screenshot on failure.
3. **Triage.** A failure gets an immediate re-run and a check for deterministic environment
   signals (5xx, timeout, 429, auth expiry), then a triage agent labels it `real_regression`,
   `flaky` or `environment_issue` with a one-line explanation.
4. **Report.** A Next.js dashboard shows the pass/fail trend per run, the flaky rate, each case's
   steps, triage and raw evidence, with a live SSE trace while a suite runs. It also exports the
   **UAT sign-off document** (Markdown, with tester sign-off lines) or a CSV.

### The safety floor

As in `scam-checker-agent`, the model can annotate but cannot override. A failed assertion is
stored as `fail`, full stop: the triage label is metadata. The label is also clamped to what the
evidence supports: `flaky` only if the immediate re-run actually passed, and `environment_issue`
only if the evidence contains an environment signal. Otherwise it becomes `real_regression`, with
`floor_applied` recording what the model claimed (`src/uatpilot/triage.py`). The same rules run
without the model when no API key is set, so the tool works offline.

## Targets

Both targets are **public sandboxes built for practising test automation**, so no one's
production system takes the traffic. Requests are rate-limited and only one suite runs at a time.
Hosts outside the allowlist are refused, which also blocks SSRF on a deployed instance.

| Target | Kind | Sandbox |
|---|---|---|
| [Restful-Booker](https://restful-booker.herokuapp.com) | API (httpx) | a booking API with known, deliberate defects |
| [Sauce Demo](https://www.saucedemo.com) | Web UI (Playwright) | a shop with test accounts |

The example requirements docs (`examples/requirements/`) are written the way a business team
would write them, stating the *intended* behaviour. Against the real Restful-Booker sandbox that
surfaces six genuine defects. The sandbox accepts a negative price and a checkout-before-checkin
booking, returns `500` for an incomplete booking, `200` for wrong credentials and `201` for a
delete, and its `PUT` keeps a field that a full replace should drop. These are reported as
regressions with evidence. The Sauce Demo suite passes 13/13 for the standard account.

## Results so far

| Suite | Triage | Injected bugs detected | Triage accuracy | Failed runs shown as pass |
|---|---|---|---|---|
| Hand-written seed suite (Restful-Booker, 16 cases) | rules | **18/18** (2 more masked by sandbox defects) | **15/15** | 0 |
| Agent-generated suite | Claude | *not measured yet* | *not measured yet* | |

**Read the first row for what it is.** The same person wrote the seed suite and the injected bugs,
so a 100% detection rate there shows that the runner, proxy and floor work end to end. It says
nothing about how good the *agent's* test cases are. The second row is the number that matters,
and it needs an API key: `uatpilot generate ...` followed by `uatpilot eval <new-set-id>`. The
two masked bugs (`put_ignores_update`, `delete_is_noop`) break criteria that the real sandbox
already fails, so they can't be measured either way.

Method, caveats and per-bug results: [`evals/README.md`](evals/README.md).

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium

uatpilot import examples/suites/restful-booker.seed.json   # hand-written fixture suites
uatpilot import examples/suites/saucedemo.seed.json
uatpilot list
uatpilot run <requirement-set-id>                      # real runs against the sandbox
uatpilot export <requirement-set-id> --out signoff.md  # the UAT sign-off document

# with ANTHROPIC_API_KEY set: have the agent write a suite from a requirements doc
uatpilot generate examples/requirements/restful-booker.md \
  --base-url https://restful-booker.herokuapp.com --kind api --run
uatpilot generate examples/requirements/saucedemo.md \
  --base-url https://www.saucedemo.com --kind ui --run

uatpilot eval <api-requirement-set-id>                 # injected-bug detection + triage accuracy
```

Dashboard: `uatpilot serve` (API on :8000) and `cd web && npm install && npm run dev` (:3000).
Docker: `docker build -t uatpilot . && docker run -p 8000:8000 uatpilot` serves both on :8000.
Deploying: [`docs/deploy.md`](docs/deploy.md).

## Architecture

```
requirements.md ──▶ generation agent (Claude tool use) ──strict schema──▶ test_cases (SQLite)
                     tools: http_probe / inspect_page,                        │
                            submit_test_cases                                 ▼
                                                         runner: httpx  |  Playwright (Chromium)
                                                                         │ pass/fail + evidence
                                                                         ▼
                                     fail ─▶ immediate re-run ─▶ env signals ─▶ triage agent
                                                                         │  (label clamped by the floor)
                                                                         ▼
                       runs + run_batches (SQLite) ──▶ FastAPI (+SSE) ──▶ Next.js dashboard, MD/CSV export
```

| Path | What |
|---|---|
| `src/uatpilot/models.py` | Strict schemas: test cases, API/UI executable specs, triage |
| `src/uatpilot/llm.py` | Hand-written tool-use loop shared by both agents (streams every step) |
| `src/uatpilot/generator.py` | Generation agent and its tools |
| `src/uatpilot/runner/` | `api.py` (httpx, JSON paths, captures), `ui.py` (Playwright, page inspection) |
| `src/uatpilot/triage.py` | Environment signals, rules triage, triage agent, the floor |
| `src/uatpilot/pipeline.py` | Run a suite: execute → re-run → triage → store |
| `src/uatpilot/api.py` | FastAPI: reads, SSE run/generate streams, exports, static dashboard |
| `src/uatpilot/evals/` | Fault-injection proxy, bug catalogue, eval harness |
| `web/` | Next.js dashboard |

Stack: Python 3.11+, FastAPI, Pydantic, Anthropic SDK (`claude-opus-5-5`, adaptive thinking,
server-side refusal fallback), httpx, Playwright, SQLite, Next.js 16, Docker, GitHub Actions.

## Changes from the original plan

The design is in [`docs/DESIGN.md`](docs/DESIGN.md). These are the deliberate changes, with the reason
for each:

- **Name:** AuditPilot → **UATPilot**.
- **Target:** public sandboxes instead of a buggy demo API built into the repo. To keep the
  injected-bug eval honest anyway, bugs are injected by a local proxy in front of the sandbox
  rather than in the sandbox itself (see `evals/README.md`).
- **Playwright UI testing is in the MVP**, not a stretch goal, including an `inspect_page` tool so
  the agent writes selectors from real page attributes.
- **Schema additions:** a `run_batches` table (to group one "Run suite" click for the trend chart),
  `requirement_ref` on each case (traceability back to the acceptance criterion), and `seq`,
  `duration_ms` and `triage_json` columns.
- **Eval set:** 20 injected bugs against one requirements doc rather than 20-30 separate docs, plus
  a labelled triage set. The detection rate excludes bugs masked by the sandbox's own defects.
- **SQLite only.** No Postgres: one service, with a Render disk for history.

## Tests

`pytest` runs 50+ offline tests in CI: a scripted fake Claude client, mocked HTTP, a local
fake booking API for the eval harness, and real headless Chromium against a page on 127.0.0.1.
No network and no API key needed.

MIT licensed.
