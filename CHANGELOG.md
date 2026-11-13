# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-11-14

### Added
- Free model providers: Google Gemini, Groq and OpenRouter through one OpenAI-compatible backend,
  picked from whichever key is set; optional local `.env`. Claude stays supported.
- Dashboard redesign (plan in `docs/UI-NOTES.md`): a sign-off verdict, the run matrix (cases x
  runs, filling live during a run), a case side sheet with history, Ctrl/Cmd-K command menu,
  deep links, theme switch, SVG icons, IBM Plex type.
- API: `GET /api/requirement-sets/{id}/matrix`, `GET /api/runs/{id}`.

### Changed
- Triage engine `claude` is now `llm`, with the model recorded on each decision (`claude` is still
  read for stored runs).

## [0.2.1] - 2026-11-14

### Added
- Dashboard: attach an OpenAPI spec (JSON/YAML) in the Generate panel; it's read in the browser
  and sent as text.
- CSV export: `since_last_run` column (new failure / fixed / still failing).
- Generation trace shows how many acceptance criteria were found and whether a spec was used.
- README screenshot, CHANGELOG.

### Fixed
- Generate panel layout on narrow screens.

## [0.2.0] - 2026-11-14

Features adopted after reviewing Schemathesis, Qodo Cover, Allure, ReportPortal, Hurl/Step CI and
Shortest.

### Added
- OpenAPI/Swagger spec as a generation input (CLI `--openapi`, API `openapi` field), condensed
  with `$ref`s resolved.
- `matches_schema` assertion: whole-response JSON Schema conformance.
- Coverage gate: every acceptance criterion must be covered or declared `not_covered` with a
  reason. Traceability matrix in the sign-off document, API and dashboard.
- History-aware results: new failure / fixed / still failing against the previous run, and
  Allure's history rule as evidence for `flaky`.
- `test_defect` triage label, allowed only with a deterministic signal.
- Reuse of earlier triage decisions for identical failures (reviewer decisions first), and
  reviewer relabelling through the API and dashboard.
- JUnit XML export.
- `SECURITY.md`.

### Changed
- SQLite schema gets additive, idempotent migrations; inserts name their columns.

### Fixed
- Docker image: install `libatomic1`, needed by Playwright's Node driver on `python:slim`.

## [0.1.0] - 2026-11-13

### Added
- Generation agent (Claude tool use) with `http_probe` / `inspect_page` and a strict schema.
- API runner (httpx) and UI runner (Playwright), against public practice sandboxes, behind a host
  allowlist and a rate limit.
- Triage: immediate re-run, environment signals, triage agent and the safety floor; a rules
  engine fallback without an API key.
- FastAPI backend with SSE streams; Next.js dashboard; Markdown/CSV sign-off export.
- Fault-injection proxy eval: 20 labelled bugs plus a labelled triage set.
- Docker image, Render blueprint, GitHub Actions CI.
