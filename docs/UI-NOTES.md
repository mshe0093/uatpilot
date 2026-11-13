# UI notes: how the best AI-built interfaces are made, and the plan for UATPilot

Research for the dashboard redesign (2026-11-14). Sources are the highest-starred repos that
people use to get AI coding agents to produce good UI, plus the dashboard templates those agents
most often start from. Star counts at the time of reading.

## What the sources say

### anthropics/skills, `frontend-design` (194k)
- Ground the design in the subject. The product's world (its vocabulary, artefacts, rituals) is
  where distinctive choices come from, not a generic "dashboard look".
- Work in two passes: write a compact token plan (4-6 named colours, typefaces and roles, an
  ASCII layout, principles), check it against the brief for anything you'd produce for *any*
  similar page, revise, *then* code.
- Spend boldness in one place. One memorable element; everything around it quiet.
- Known tells of generated UI to avoid unless the brief asks for them: cream background with a
  serif and a terracotta accent; near-black with one acid-green accent; the "SaaS card kit"
  (identical rounded cards, one radius everywhere, the same soft grey shadow, gradient washes);
  ALL-CAPS eyebrow labels above every heading; meta strings joined with middle dots; "WORD —
  fragment" labels; monospace for small labels; "→" on every button; numbered 01/02/03 markers
  on content that isn't a sequence; fade-and-slide on every section.
- Motion only where it answers an action or draws attention once.
- Copy: name things by what the user does, sentence case, errors say what happened and how to
  fix it, empty states invite the next action.

### nextlevelbuilder/ui-ux-pro-max-skill (149k)
- Priority order for UI quality: accessibility (4.5:1 contrast, keyboard, labels) → touch targets
  (44px) → performance (no layout shift) → style fit → typography/colour (semantic tokens, 16px
  base) → motion (meaning, reduced-motion) → navigation (deep links) → charts (legends, tooltips,
  never colour alone).
- SVG icons, never emoji or text glyphs as icons.
- Its product database maps tools like ours to **Status page / incident management** ("service
  status matrix, incident timeline, severity badges, status green / incident red / maintenance
  amber on a neutral base") and **Developer tool** ("keyboard shortcuts, fast, blue focus").
- Dashboards want a dense spacing scale (8-32px), not marketing spacing.

### VoltAgent/awesome-design-md (127k)
- A `DESIGN.md` per brand: tokens for colour, type scale, radii and the *rules* for using them
  (for example Linear: one chromatic accent, used only on focus rings and a few CTAs, "never
  decoratively"; hairline borders instead of shadows; negative tracking on display type).
- Sentry (failure triage, our closest neighbour) is purple/lime and playful; PostHog is
  cream/olive with mascots. Both show that developer tools can have a personality, but neither
  fits a *sign-off* tool, whose job is trust and legibility.

### shadcn-ui/ui (128k), satnaing/shadcn-admin (17k), next-shadcn-dashboard-starter (7k)
- The shared baseline: app shell with a sidebar, light/dark, responsive, accessible primitives
  (Radix), a global **command palette (Cmd/Ctrl-K)**, data tables with search/filter/sort synced
  to the URL, side sheets for detail, toasts for completed actions, skeletons while loading.
- These are table stakes. On their own they produce the same-looking admin panel as everyone
  else, which is exactly the "templated" result the frontend-design skill warns about.

### magicui (23k), motion (35k)
- Animated components exist for everything; the restraint advice above says to use one
  orchestrated moment rather than decorating every surface.

## What was wrong with the current dashboard

Measured against those notes:
- It is the SaaS card kit: four identical KPI tiles (big number, small label), then identical
  rounded cards with the same border for every section.
- The most important question a release manager has ("can we sign this off, and if not, what is
  blocking it?") is not answered anywhere; you have to read a chart and a table.
- History per case (the thing that shows a flaky test or a new regression) is hidden in the
  database; the chart only shows totals per run.
- Status icons are text glyphs (✓ ✕ ~ ! ⚙), the expanded case detail pushes the whole table
  down, and there is no keyboard navigation beyond tabbing.

## Design plan

**Subject.** A release sign-off tool: the artefacts of its world are the sign-off sheet, the
requirement ID, the test run, the defect, and the verdict "ready / not ready". Audience: a
release manager or QA lead deciding whether to ship, and the engineer fixing what blocks it.

**The one memorable element: the run matrix.** Test cases as rows, runs as columns, one small
cell per result, newest on the right. A regression is a red tail on a row; a flaky test is a row
that flickers; a fix is the moment a row turns green. It is the status-page "service matrix"
and Allure's history in a single picture, and it is specific to what UATPilot actually stores.
During a live run, the newest column fills cell by cell as results stream in: the only
non-user-triggered motion on the page.

**Verdict first.** Each suite opens with a plain sentence, not a KPI tile: "Not ready to sign
off. 6 of 17 criteria are failing." Supporting numbers sit inline beside it.

**Tokens.**

| Role | Light | Dark | Use |
|---|---|---|---|
| canvas | `#F6F7F9` | `#0E1116` | page |
| surface | `#FFFFFF` | `#151A21` | panels |
| ink | `#141922` | `#E6E9EF` | text |
| muted | `#5A6474` | `#98A2B3` | secondary text (4.5:1 on both surfaces) |
| hairline | `#E2E6EC` | `#262D38` | borders; no drop shadows on panels |
| accent | `#2D5BE3` | `#7C9CFF` | focus rings, the primary action, selection; never decorative |

Status colours stay the reserved dataviz status palette (pass / regression / flaky /
environment / test defect) and always come with an SVG icon and a word.

Type: **IBM Plex Sans** for the interface and **IBM Plex Mono** for things that are literally
code or identifiers (case IDs, requirement IDs, JSON evidence), self-hosted via Fontsource.
Plex comes from engineering documentation, which is what a sign-off record is; it is not the
default Inter/Geist look. Scale 13 / 14 / 16 / 20 / 28, weights 400/500/600, slight negative
tracking on the verdict line only.

Radius 6px on controls, 10px on panels, 3px on matrix cells. Density: 8-32px spacing.

**Layout.**

```
┌──────────────┬─────────────────────────────────────────────────────────────┐
│ UATPilot     │ restful-booker.md        API · Restful-Booker   [Run] [Ctrl K]│
│              ├─────────────────────────────────────────────────────────────┤
│ Suites       │ Not ready to sign off.                                       │
│  ● booker 16 │ 6 of 17 criteria failing · last run 2 min ago · 10/16 passed │
│  ● sauce  13 │                                                              │
│              │ Run matrix ───────────────────────────────── runs #1 … #8 →  │
│ Generate     │ TC001 Health check …        ■■■■■■■■                          │
│              │ TC004 Missing fields → 400  ■■■■■■■■  red tail = regression   │
│ Model: …     │ …                                                            │
│              │ [Overview] [Cases] [Traceability]                            │
└──────────────┴─────────────────────────────────────────────────────────────┘
          a case opens in a side sheet (steps, triage, evidence, review)
```

Left-aligned throughout; numbers right-aligned in tables. On phones the sidebar becomes a top
bar and the matrix scrolls horizontally inside its panel (never the page).

**Principles.**
1. Answer "can we ship?" before anything else.
2. History is the evidence: show it per case, not just as totals.
3. Colour only for state and for the one accent; everything else is ink and hairlines.
4. Every action is reachable from the keyboard (Ctrl/Cmd-K, arrow keys in the matrix, Esc closes
   the sheet).
5. Copy names user actions ("Run suite", "Download sign-off"), sentence case, no decorative
   labels.

**Review against the brief.** Things in the first draft that were generic defaults, and what
replaced them:
- A row of four KPI tiles → the verdict sentence with inline numbers.
- A stacked-bar trend chart as the hero → the run matrix (the stacked bars move to the Overview
  tab as a secondary view).
- "Card for every section" → one surface per view, divided by hairlines, no shadows.
- Text glyph icons → Lucide SVG icons with labels.
- Inline row expansion → a side sheet, so the table doesn't jump.
