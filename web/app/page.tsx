"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL, getJSON, streamEvents } from "@/lib/api";
import type { Batch, Health, RequirementSet, RequirementSetSummary, Run, Stats, StreamEvent } from "@/lib/types";
import { CaseTable } from "@/components/CaseTable";
import { GeneratePanel } from "@/components/GeneratePanel";
import { Trace } from "@/components/Trace";
import { TrendChart } from "@/components/TrendChart";

interface Overall { overall: Stats; requirement_sets: number; test_cases: number }

const pct = (x: number) => `${Math.round(x * 1000) / 10}%`;

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [overall, setOverall] = useState<Overall | null>(null);
  const [sets, setSets] = useState<RequirementSetSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RequirementSet | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [live, setLive] = useState<StreamEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [tab, setTab] = useState<"suites" | "generate">("suites");

  const refreshTop = useCallback(async () => {
    try {
      const [h, o, s] = await Promise.all([
        getJSON<Health>("/api/health"),
        getJSON<Overall>("/api/stats"),
        getJSON<RequirementSetSummary[]>("/api/requirement-sets"),
      ]);
      setHealth(h); setOverall(o); setSets(s);
      setSelected((cur) => cur ?? s[0]?.id ?? null);
      setError(null);
    } catch {
      setError(`Can't reach the UATPilot API at ${API_URL || "this origin"}. Is \`uatpilot serve\` running?`);
    }
  }, []);

  const refreshSet = useCallback(async (id: string) => {
    const [d, b] = await Promise.all([
      getJSON<RequirementSet>(`/api/requirement-sets/${id}`),
      getJSON<Batch[]>(`/api/batches?requirement_set_id=${id}`),
    ]);
    setDetail(d); setBatches(b);
    const last = b[b.length - 1];
    if (last && last.total > 0) {
      const r = await getJSON<{ runs: Run[] }>(`/api/batches/${last.id}`);
      setRuns(Object.fromEntries(r.runs.map((x) => [x.test_case_id, x])));
    } else {
      setRuns({});
    }
  }, []);

  useEffect(() => { void refreshTop(); }, [refreshTop]);
  useEffect(() => { if (selected) void refreshSet(selected); }, [selected, refreshSet]);

  async function runSuite() {
    if (!selected) return;
    setRunning(true); setLive([]); setError(null);
    try {
      await streamEvents("/api/run", { requirement_set_id: selected }, (ev) => {
        setLive((prev) => [...prev, ev]);
        if (ev.type === "case_result") setRuns((prev) => ({ ...prev, [ev.run.test_case_id]: ev.run }));
      }, token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
      await Promise.all([refreshTop(), refreshSet(selected)]);
    }
  }

  const last = batches[batches.length - 1];

  return (
    <main className="page">
      <header className="top">
        <div>
          <h1>UATPilot</h1>
          <p>
            Requirements in, UAT test cases out. An agent writes the cases, they run for real
            against the target, and every failure is triaged. A failed check is never reported
            as a pass.
          </p>
        </div>
        <div className="toolbar" style={{ alignItems: "flex-start" }}>
          {health && (
            <>
              <span className="pill">Triage: {health.claude_available ? "Claude" : "rules (no API key)"}</span>
              {health.read_only && <span className="pill">Read-only demo</span>}
              <span className="pill">v{health.version}</span>
            </>
          )}
        </div>
      </header>

      {error && <div className="card error" style={{ marginBottom: 16 }}>{error}</div>}

      {overall && (
        <section className="grid kpis" style={{ marginBottom: 16 }}>
          <Kpi label="Test cases" value={String(overall.test_cases)} sub={`${overall.requirement_sets} requirement sets`} />
          <Kpi label="Executions recorded" value={String(overall.overall.runs)} sub="every run kept for the trend" />
          <Kpi label="Pass rate" value={pct(overall.overall.pass_rate)} sub={`${overall.overall.failed} failed executions`} />
          <Kpi label="Flaky rate" value={pct(overall.overall.flaky_rate)} sub="failed, then passed on re-run" />
        </section>
      )}

      <div className="toolbar" style={{ marginBottom: 12 }} role="tablist">
        <button className={`btn ${tab === "suites" ? "primary" : ""}`} role="tab" aria-selected={tab === "suites"}
          onClick={() => setTab("suites")}>Suites & runs</button>
        <button className={`btn ${tab === "generate" ? "primary" : ""}`} role="tab" aria-selected={tab === "generate"}
          onClick={() => setTab("generate")}>Generate</button>
        {health?.read_only && (
          <input placeholder="Write token" value={token} onChange={(e) => setToken(e.target.value)}
            type="password" className="btn" style={{ marginLeft: "auto", width: 160 }} aria-label="Write token" />
        )}
      </div>

      {tab === "generate" && health && (
        <GeneratePanel enabled={health.generation_available && (!health.read_only || token !== "")}
          allowedHosts={health.allowed_hosts} token={token}
          onSaved={(id) => { void refreshTop(); setSelected(id); }} />
      )}

      {tab === "suites" && (
        <div className="grid main">
          <aside className="card">
            <h2>Requirement sets</h2>
            {sets.length === 0 && <p className="muted small">None yet. Import a seed suite or generate one.</p>}
            <ul className="sets">
              {sets.map((s) => (
                <li key={s.id}>
                  <button aria-pressed={selected === s.id} onClick={() => setSelected(s.id)}>
                    <span className="name">{s.source_name}</span>
                    <span className="meta">{s.target_kind.toUpperCase()} · {s.case_count} cases · {s.generated_by.split(":")[0] === "claude" ? "written by Claude" : "hand-written fixture"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          {detail && (
            <section className="grid">
              <div className="card">
                <div className="toolbar" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                  <div>
                    <h2 style={{ margin: 0 }}>{detail.source_name}</h2>
                    <span className="muted small">{detail.base_url} · {detail.generated_by}</span>
                  </div>
                  <div className="toolbar">
                    <button className="btn primary" onClick={runSuite}
                      disabled={running || (health?.read_only && !token)}>
                      {running ? "Running…" : "Run suite"}
                    </button>
                    <a className="btn" href={`${API_URL}/api/requirement-sets/${detail.id}/export.md`}>UAT sign-off (.md)</a>
                    <a className="btn" href={`${API_URL}/api/requirement-sets/${detail.id}/export.csv`}>CSV</a>
                  </div>
                </div>
                <h3>Results per run</h3>
                <TrendChart batches={batches} />
                {last && (
                  <p className="small muted">
                    Latest run: {last.passed}/{last.total} passed · {last.regressions} regressions ·{" "}
                    {last.flaky} flaky · {last.env_issues} environment · flaky rate for this suite{" "}
                    {pct(detail.stats.flaky_rate)}
                  </p>
                )}
              </div>

              {live.length > 0 && (
                <div className="card">
                  <h3>Live run</h3>
                  <Trace events={live} />
                </div>
              )}

              <div className="card">
                <h3>Test cases <span className="muted small">(click a row for steps, triage and evidence)</span></h3>
                <CaseTable cases={detail.test_cases} runs={runs} />
              </div>

              <details className="card">
                <summary><strong>Source requirements</strong></summary>
                <pre className="evidence mono">{detail.raw_text}</pre>
              </details>
            </section>
          )}
        </div>
      )}
    </main>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="sub">{sub}</div>
    </div>
  );
}
