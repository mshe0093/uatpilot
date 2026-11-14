# Review of the agent-written suites (metric 4)

**Question:** what share of the test cases the generation agent writes would a QA reviewer accept
without editing?

**Reviewer:** an AI model (Claude) acting as the QA reviewer, at the project owner's request. It
is not an independent human review, and the reviewer also helped build the tool, so read the
numbers as an informed estimate, not a measurement. Each case was judged against its
acceptance criterion in [`examples/requirements/`](../examples/requirements/) using its
executable spec (what actually runs), not only its prose.

**Rules:**
- *Accept* means it could go into a UAT pack as is. It runs, it checks what the criterion asks,
  and it can fail when the behaviour is wrong.
- *Minor edit* means it is right in substance but misses a check the criterion names, or relies
  on shared data.
- *Rework* means it would pass when the behaviour is wrong, or it fails when the behaviour is
  right.

## Result

| Suite | Cases | Accept | Minor edit | Rework | Accepted unedited |
|---|---|---|---|---|---|
| Restful-Booker API (`gemini-3.5-flash`) | 17 | 10 | 6 | 1 | 59% |
| Sauce Demo UI (`gemini-3.1-flash-lite`) | 13 | 8 | 3 | 2 | 62% |
| **Total** | **30** | **18** | **9** | **3** | **60%** |

All 30 cases map to the right criterion, use real endpoints and selectors, and none invent
fields. The edits needed are about *how strictly* they check, which matches what the
fault-injection eval showed: the API suite caught every injected bug, yet surfaced 4 of the 6
real sandbox defects where the hand-written suite surfaced all 6.

## Restful-Booker (API)

| Case | Criterion | Verdict | Why |
|---|---|---|---|
| TC001 | AC-1.1 | Accept | 2xx check on `/ping`. |
| TC002 | AC-2.1 | Minor edit | Checks `bookingid` exists, not that it is numeric. |
| TC003 | AC-2.2 | Accept | Creates, then reads back name, price and dates. |
| TC004 | AC-2.3 | Accept | Exactly `400` for a first-name-only booking. |
| TC005 | AC-2.4 | Accept | 4xx for a negative price. |
| TC006 | AC-2.5 | Accept | 4xx for checkout before checkin. |
| TC007 | AC-3.1 | Minor edit | Checks only the first item; "each contains `bookingid`" needs a schema check. |
| TC008 | AC-3.2 | **Rework** | Asserts the filtered list has *some* `bookingid`, not the one just created. A filter that ignored its parameters would pass. |
| TC009 | AC-3.3 | Accept | `404` for an unknown id. |
| TC010 | AC-4.1 | Accept | `200` and a non-empty token. |
| TC011 | AC-4.2 | Accept | `401` and no token. |
| TC012 | AC-4.3 | Minor edit | Targets the shared booking `1` instead of one it created. |
| TC013 | AC-5.1 | Accept | PUT then GET both show the new values. |
| TC014 | AC-5.2 | Minor edit | "Every other field unchanged" is checked for 2 of 5 fields. |
| TC015 | AC-6.1 | Minor edit | Accepts any 2xx; the criterion names `204` as the standard. |
| TC016 | AC-6.2 | Accept | Delete, then `404`. |
| TC017 | NFR-1 | Minor edit | Times the shared booking `1`, which can disappear on a public sandbox. |

## Sauce Demo (UI)

| Case | Criterion | Verdict | Why |
|---|---|---|---|
| TC001 | AC-1.1 | Accept | URL and "Products" heading. |
| TC002 | AC-1.2 | Minor edit | Checks the error, not that the customer stays on the login page. |
| TC003 | AC-1.3 | Minor edit | Same gap as TC002. |
| TC004 | AC-1.4 | Accept | |
| TC005 | AC-2.1 | Accept | Exactly 6 items. |
| TC006 | AC-2.2 | Accept | First price after sorting is $7.99. |
| TC007 | AC-2.3 | Accept | |
| TC008 | AC-3.1 | Accept | |
| TC009 | AC-3.2 | **Rework** | Expects both product names as one string; the cart shows them on separate lines, so it fails on a correct app. Triage labels it `test_defect`. |
| TC010 | AC-3.3 | Accept | Item and badge both gone. |
| TC011 | AC-4.1 | Minor edit | Skips the overview page that the criterion lists. |
| TC012 | AC-4.2 | Accept | |
| TC013 | AC-5.1 | **Rework** | Checks the URL contains `https://www.saucedemo.com/`, which every page's URL contains. It could never fail. |

## What changed because of this review

- **Generator:** the generator now rejects an `expect_url` value that every page on the site would
  match (TC013's flaw), with a message telling the model what to check instead.
- **Triage:** the `test_defect` signal for split text (TC009's flaw) was added earlier, after the
  live run.
- TC008's flaw (asserting presence instead of identity) is harder to catch mechanically. It is
  the main gap left: an assertion comparing a filtered result to a captured id is something to
  ask for in the generation prompt and review by hand.
