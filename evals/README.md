# Evaluation

Small, honest, hand-checked. **This is a smoke test, not a benchmark**: the same person wrote the
requirements, the injected bugs, the seed suite and the system under evaluation.

```bash
uatpilot eval <api-requirement-set-id>            # writes evals/results/<tag>-<engine>-<time>.{json,md}
uatpilot eval <id> --only ping_500,slow_reads     # a subset of the bug catalogue
```

## Why a fault-injection proxy

The target is a public sandbox ([Restful-Booker](https://restful-booker.herokuapp.com)), so bugs
can't be planted in it. The eval starts a local proxy (`src/uatpilot/evals/proxy.py`) in front of
the real sandbox and points the runner at the proxy. With no mutation active, the proxy just
forwards. With one active, it corrupts the handling in one specific, labelled way, for example
"the create response echoes `totalprice + 1`" or "DELETE without a token succeeds". The catalogue
of 20 bugs, each tagged with the acceptance criterion it breaks, is in
`src/uatpilot/evals/mutations.py`.

## Metrics

1. **Detection rate.** First the whole suite runs twice through the proxy with no mutation. Cases
   that pass both times are the *stable* set. Cases that fail are the sandbox's own defects
   (Restful-Booker has several, e.g. it returns 500 for an incomplete booking and 201 for a
   delete), and both those and any unstable cases are excluded. Then, for each mutation, the stable
   cases run with the mutation on, and the bug counts as **detected** if at least one of them
   fails.
   If no stable case covers the broken criterion (because that criterion already fails on the
   real sandbox), the bug is reported as **masked**: it can't be measured, so it's left out of the
   denominator rather than counted as caught or missed.
2. **Triage accuracy.** Five scenarios with a known correct label: a failure that happens once
   (`flaky`), 503s / timeouts / 429s on every call (`environment_issue`), and a consistent wrong
   status (`real_regression`). Three stable cases are run through each scenario on the full path
   (fail → immediate re-run → triage), and the label is compared with the known answer.
3. **Never flipped to pass.** In every triage scenario, every failed run must still be stored as
   `fail`. The report records this as a yes/no.
4. **Generation quality** (plan metric 2: the share of agent-written cases a QA reviewer accepts
   unedited): 18 of 30 (60%), with 9 needing a minor edit and 3 a rework. The reviewer was an AI
   model, not an independent human; per-case verdicts are in
   [`review-gemini-suites.md`](review-gemini-suites.md).

## Status and caveats

- **Two suites are measured.** The hand-written seed suite (`examples/suites/
  restful-booker.seed.json`, rules triage) tests the runner, proxy and triage plumbing. The
  agent-written suite (`results/gemini/`, written by `gemini-3.5-flash`, triaged by
  `gemini-3.1-flash-lite`) is the one that says something about the generation agent: 20/20
  detection, 15/15 triage. Its cases are looser on two criteria the real sandbox already fails,
  so 0 mutations were masked for it (2 for the seed suite).
- Generation quality (metric 4) was judged by an AI reviewer, not an independent human, so treat
  the 60% as an estimate.
- **One triage rule was changed after a smoke run.** The first rule labelled any reproduced 5xx a
  defect, which mislabelled the `env_503` scenario. It now treats 502/503/504 as environment even
  when they repeat, and a reproduced plain 500 as a defect. That change was made after seeing eval
  output, so the triage number for that scenario is not an out-of-sample measurement.
- In the same smoke run, one scenario (`real_wrong_status`) rewrote 200 to 299, which the ping
  case's `< 300` assertion still accepted. That was a harness bug: it now rewrites to 409.
- Results depend on a public sandbox that is shared and reset every few minutes, so expect small
  run-to-run differences.

Results are in `results/`, one JSON and one Markdown file per run.
