"use client";

import { FilePlus2, FolderOpen, Moon, Play, Search, Sun } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, getJSON, streamEvents } from "@/lib/api";
import { author, suiteName } from "@/lib/author";
import type { Batch, Health, Matrix, RequirementSet, RequirementSetSummary, Run, StreamEvent } from "@/lib/types";
import { CaseSheet } from "@/components/CaseSheet";
import { CaseTable, TraceTable } from "@/components/CaseTable";
import { CommandMenu, type Action } from "@/components/CommandMenu";
import { GeneratePanel } from "@/components/GeneratePanel";
import { RunMatrix } from "@/components/RunMatrix";
import { Trace } from "@/components/Trace";
import { TrendChart } from "@/components/TrendChart";
import { Verdict } from "@/components/Verdict";

type Tab = "history" | "cases" | "requirements" | "trend";
const TABS: { id: Tab; label: string }[] = [
  { id: "history", label: "Run history" },
  { id: "cases", label: "Test cases" },
  { id: "requirements", label: "Requirements" },
  { id: "trend", label: "Trend" },
];

function readUrl() {
  if (typeof window === "undefined") return new URLSearchParams();
  return new URLSearchParams(window.location.search);
}

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [sets, setSets] = useState<RequirementSetSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"suite" | "generate">("suite");
  const [tab, setTab] = useState<Tab>("history");
  const [detail, setDetail] = useState<RequirementSet | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [live, setLive] = useState<Record<string, Run> | null>(null);
  const [liveTotal, setLiveTotal] = useState(0);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [open, setOpen] = useState<{ caseId: string; runId: string | null } | null>(null);
  const [cmd, setCmd] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);

  // ---- URL state (deep links): ?set=…&tab=…&case=… ------------------------------------------
  useEffect(() => {
    const q = readUrl();
    if (q.get("set")) setSelected(q.get("set"));
    if (q.get("tab") && TABS.some((t) => t.id === q.get("tab"))) setTab(q.get("tab") as Tab);
    if (q.get("view") === "generate") setView("generate");
    if (q.get("case")) setOpen({ caseId: q.get("case")!, runId: null });
    try {
      const saved = localStorage.getItem("uatpilot-theme");
      if (saved === "light" || saved === "dark") setTheme(saved);
    } catch { /* storage blocked: follow the system theme */ }
  }, []);
  useEffect(() => {
    const q = new URLSearchParams();
    if (view === "generate") q.set("view", "generate");
    else {
      if (selected) q.set("set", selected);
      if (tab !== "history") q.set("tab", tab);
      if (open) q.set("case", open.caseId);
    }
    const qs = q.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`);
  }, [selected, tab, open, view]);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
  }, [theme]);

  // ---- data ------------------------------------------------------------------------------------
  const refreshTop = useCallback(async () => {
    try {
      const [h, s] = await Promise.all([
        getJSON<Health>("/api/health"),
        getJSON<RequirementSetSummary[]>("/api/requirement-sets"),
      ]);
      setHealth(h); setSets(s);
      setSelected((cur) => (cur && s.some((x) => x.id === cur) ? cur : s[0]?.id ?? null));
      setError(null);
    } catch {
      setError(`Can’t reach the UATPilot API at ${API_URL || "this address"}. Start it with “uatpilot serve”.`);
    }
  }, []);

  // Switching suites quickly starts overlapping loads; only the latest one may update the page,
  // or a slow response for the previous suite would show its data under the new suite's name.
  const loading = useRef<string | null>(null);
  const refreshSet = useCallback(async (id: string) => {
    loading.current = id;
    const [d, b, m] = await Promise.all([
      getJSON<RequirementSet>(`/api/requirement-sets/${id}`),
      getJSON<Batch[]>(`/api/batches?requirement_set_id=${id}`),
      getJSON<Matrix>(`/api/requirement-sets/${id}/matrix`),
    ]);
    if (loading.current !== id) return;
    setDetail(d); setBatches(b); setMatrix(m);
    const last = b[b.length - 1];
    if (last && last.total > 0) {
      const r = await getJSON<{ runs: Run[] }>(`/api/batches/${last.id}`);
      if (loading.current !== id) return;
      setRuns(Object.fromEntries(r.runs.map((x) => [x.test_case_id, x])));
    } else {
      setRuns({});
    }
  }, []);

  useEffect(() => { void refreshTop(); }, [refreshTop]);
  useEffect(() => {
    if (selected) void refreshSet(selected).catch(() => setError("That suite couldn’t be loaded."));
  }, [selected, refreshSet]);

  // ---- actions ---------------------------------------------------------------------------------
  const canWrite = !health?.read_only || token !== "";
  const running = live !== null;

  const runSuite = useCallback(async () => {
    if (!selected || running) return;
    setView("suite"); setTab("history"); setEvents([]); setError(null);
    setLive({}); setLiveTotal(detail?.test_cases.length ?? 0);
    let summary = "";
    try {
      await streamEvents("/api/run", { requirement_set_id: selected }, (ev) => {
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "batch_start") setLiveTotal(ev.total);
        if (ev.type === "case_result") setLive((prev) => ({ ...(prev ?? {}), [ev.run.test_case_id]: ev.run }));
        if (ev.type === "batch_done") summary = `Run finished: ${ev.pass} passed, ${ev.fail} failed.`;
        if (ev.type === "error") setError(ev.message);
      }, token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      await Promise.all([refreshTop(), refreshSet(selected)]);
      setLive(null);
      if (summary) setToast(summary);
    }
  }, [selected, running, detail, token, refreshTop, refreshSet]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((v) => !v); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const download = (path: string) => () => { window.location.href = `${API_URL}${path}`; };
  const toggleTheme = () => {
    const isDark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    setTheme(next);
    try { localStorage.setItem("uatpilot-theme", next); } catch { /* not persisted */ }
  };
  const actions: Action[] = [
    { id: "run", label: "Run this suite", run: () => void runSuite(), disabled: !selected || running || !canWrite },
    { id: "signoff", label: "Download sign-off document", run: download(`/api/requirement-sets/${selected}/export.md`), disabled: !selected },
    { id: "csv", label: "Download results as CSV", run: download(`/api/requirement-sets/${selected}/export.csv`), disabled: !selected },
    { id: "junit", label: "Download JUnit XML", run: download(`/api/requirement-sets/${selected}/junit.xml`), disabled: !selected },
    { id: "generate", label: "Write a new suite from requirements", run: () => setView("generate") },
    { id: "theme", label: "Switch light / dark theme", run: toggleTheme },
  ];

  const caseIdFor = useMemo(() => {
    const byShort = new Map((detail?.test_cases ?? []).map((c) => [c.id.split("-").pop()!, c.id]));
    return (short: string) => byShort.get(short);
  }, [detail]);
  const openCase = detail?.test_cases.find((c) => c.id === open?.caseId) ?? null;
  const last = batches[batches.length - 1];
  const current = sets.find((s) => s.id === selected);
  const written = current
    ? current.generated_by.startsWith("import:") ? "hand-written suite" : `written by ${current.generated_by.split(":").slice(1).join(":")}`
    : "";
  const done = Object.keys(live ?? {}).length;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Play size={13} fill="currentColor" aria-hidden /></span>UATPilot</div>

        <nav className="nav-group" aria-label="Suites">
          <div className="nav-heading">Suites</div>
          <ul className="nav">
            {sets.map((s) => (
              <li key={s.id}>
                <button aria-current={view === "suite" && s.id === selected}
                  onClick={() => { setView("suite"); setSelected(s.id); setOpen(null); }}>
                  <FolderOpen size={16} aria-hidden />
                  <span className="label"><span className="name">{suiteName(s)}</span><span className="by">{author(s)}</span></span>
                  <span className="count">{s.case_count}</span>
                </button>
              </li>
            ))}
            <li>
              <button aria-current={view === "generate"} onClick={() => setView("generate")}>
                <FilePlus2 size={16} aria-hidden />Write a new suite
              </button>
            </li>
          </ul>
        </nav>

        <div className="mobile-sets">
          <select aria-label="Suite" value={view === "generate" ? "__generate" : selected ?? ""}
            onChange={(e) => {
              if (e.target.value === "__generate") setView("generate");
              else { setView("suite"); setSelected(e.target.value); }
            }}>
            {sets.map((s) => <option key={s.id} value={s.id}>{suiteName(s)} · {author(s)}</option>)}
            <option value="__generate">Write a new suite</option>
          </select>
        </div>

        <div className="sidebar-foot">
          {health && (
            <span className="model">
              <i className={`model-dot ${health.llm_available ? "on" : ""}`} />
              {health.model ? `Model: ${health.model.split(":").slice(1).join(":")}` : "No model: rules-based triage"}
            </span>
          )}
          {health?.read_only && (
            <input type="password" placeholder="Write token" value={token} aria-label="Write token"
              onChange={(e) => setToken(e.target.value)} className="btn" style={{ width: "100%" }} />
          )}
          <button className="btn ghost" onClick={toggleTheme} style={{ justifyContent: "flex-start", padding: 0 }}>
            {theme === "dark" ? <Sun size={15} aria-hidden /> : <Moon size={15} aria-hidden />} Switch theme
          </button>
          {health && <span>Version {health.version}</span>}
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          {view === "generate" ? <h1>Write a new suite</h1> : (
            <>
              <h1>{current?.source_name ?? "UATPilot"}</h1>
              {current && <span className="where">{current.base_url.replace(/^https?:\/\//, "")}, {written}</span>}
            </>
          )}
          <span className="spacer" />
          <button className="btn ghost kbd-hint" onClick={() => setCmd(true)} aria-label="Open command menu">
            <Search size={15} aria-hidden /> Search <span className="kbd">Ctrl K</span>
          </button>
          {view === "suite" && selected && (
            <>
              <button className="btn" onClick={download(`/api/requirement-sets/${selected}/export.md`)}>Sign-off document</button>
              <button className="btn primary" onClick={() => void runSuite()} disabled={running || !canWrite}
                title={canWrite ? undefined : "Enter the write token to run suites"}>
                <Play size={14} fill="currentColor" aria-hidden />{running ? "Running" : "Run suite"}
              </button>
            </>
          )}
        </header>
        {running && (
          <div className="progress" role="progressbar" aria-label="Suite run progress"
            aria-valuemin={0} aria-valuemax={liveTotal} aria-valuenow={done}>
            <i style={{ width: `${liveTotal ? (done / liveTotal) * 100 : 0}%` }} />
          </div>
        )}

        <main className="content">
          {error && <div className="note error" role="alert" style={{ marginBottom: 20 }}>{error}</div>}

          {view === "generate" && health && (
            <GeneratePanel enabled={health.generation_available && canWrite} allowedHosts={health.allowed_hosts}
              token={token} onSaved={(id) => { void refreshTop(); setSelected(id); setView("suite"); }} />
          )}

          {view === "suite" && !detail && !error && sets.length === 0 && health && (
            <div className="empty">
              <strong>No suites yet</strong>
              <span>Import a hand-written suite with “uatpilot import”, or write one from a requirements document.</span>
              <button className="btn primary" onClick={() => setView("generate")}><FilePlus2 size={15} aria-hidden />Write a new suite</button>
            </div>
          )}

          {view === "suite" && detail && (
            <>
              <Verdict detail={detail} last={last} />
              <div className="tabs" role="tablist" aria-label="Suite views">
                {TABS.map((t) => (
                  <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>{t.label}</button>
                ))}
              </div>

              {tab === "history" && matrix && (
                <div className="stack">
                  <section className="panel">
                    <div className="panel-head">
                      <h3>Every case, every run</h3>
                      <span className="hint">Newest run on the right. Select a cell to see that result.</span>
                    </div>
                    <div className="panel-body">
                      <RunMatrix matrix={matrix} live={live} liveTotal={liveTotal}
                        onOpen={(caseId, runId) => setOpen({ caseId, runId: runId ?? null })} />
                    </div>
                  </section>
                  {events.length > 0 && (
                    <details className="panel" open={running}>
                      <summary className="panel-head" style={{ listStyle: "none" }}>
                        <h3>Activity</h3><span className="hint">What the runner and triage did, step by step</span>
                      </summary>
                      <div className="panel-body"><Trace events={events} /></div>
                    </details>
                  )}
                </div>
              )}

              {tab === "cases" && (
                <section className="panel">
                  <CaseTable cases={detail.test_cases} runs={runs} onOpen={(id) => setOpen({ caseId: id, runId: null })} />
                </section>
              )}

              {tab === "requirements" && (
                <section className="panel">
                  <div className="panel-head">
                    <h3>Acceptance criteria</h3>
                    <span className="hint">
                      {detail.traceability.filter((t) => t.cases.length > 0).length} of {detail.traceability.length} have a test case
                    </span>
                  </div>
                  <TraceTable rows={detail.traceability} caseIdFor={caseIdFor}
                    onOpenCase={(id) => setOpen({ caseId: id, runId: null })} />
                  <details className="panel-body" style={{ borderTop: "1px solid var(--hairline)" }}>
                    <summary>Source document</summary>
                    <pre className="evidence" style={{ marginTop: 10 }}>{detail.raw_text}</pre>
                  </details>
                </section>
              )}

              {tab === "trend" && (
                <section className="panel">
                  <div className="panel-head">
                    <h3>Results per run</h3>
                    <span className="hint">Flaky rate for this suite {Math.round(detail.stats.flaky_rate * 1000) / 10}%</span>
                  </div>
                  <div className="panel-body"><TrendChart batches={batches} /></div>
                </section>
              )}
            </>
          )}
        </main>
      </div>

      <CaseSheet testCase={openCase} runId={open?.runId ?? null} latestRun={openCase ? runs[openCase.id] : undefined}
        matrix={matrix} canWrite={canWrite} token={token} onClose={() => setOpen(null)}
        onReviewed={(r: Run) => {
          setRuns((prev) => (prev[r.test_case_id]?.id === r.id ? { ...prev, [r.test_case_id]: r } : prev));
          if (selected) void refreshSet(selected);
        }} />
      <CommandMenu open={cmd} onOpenChange={setCmd} sets={sets} cases={view === "suite" ? detail?.test_cases ?? [] : []}
        onSet={(id) => { setView("suite"); setSelected(id); }} onCase={(id) => setOpen({ caseId: id, runId: null })}
        actions={actions} />
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
