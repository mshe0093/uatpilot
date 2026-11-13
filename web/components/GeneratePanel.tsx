"use client";

import { useState } from "react";
import { streamEvents } from "@/lib/api";
import type { StreamEvent } from "@/lib/types";
import { Trace } from "./Trace";

export function GeneratePanel({ enabled, allowedHosts, token, onSaved }: {
  enabled: boolean;
  allowedHosts: string[];
  token: string;
  onSaved: (setId: string) => void;
}) {
  const [name, setName] = useState("my-requirements.md");
  const [text, setText] = useState("");
  const [kind, setKind] = useState<"api" | "ui">("api");
  const [baseUrl, setBaseUrl] = useState("https://restful-booker.herokuapp.com");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true); setError(null); setEvents([]);
    try {
      await streamEvents("/api/generate",
        { source_name: name, requirements: text, base_url: baseUrl, target_kind: kind },
        (ev) => {
          setEvents((prev) => [...prev, ev]);
          if (ev.type === "saved") onSaved(ev.requirement_set_id);
        }, token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Generate a suite from requirements</h2>
      {!enabled && (
        <p className="muted small">
          Generation calls Claude and is off on this server (no API key, or read-only mode).
          Run it locally with <span className="mono">ANTHROPIC_API_KEY</span> set, or use{" "}
          <span className="mono">uatpilot generate</span> from the CLI.
        </p>
      )}
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 120px" }}>
        <label className="field">Document name
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={!enabled} />
        </label>
        <label className="field">Target base URL
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} disabled={!enabled}
            list="hosts" />
          <datalist id="hosts">
            {allowedHosts.filter((h) => !h.startsWith("127") && h !== "localhost")
              .map((h) => <option key={h} value={`https://${h}`} />)}
          </datalist>
        </label>
        <label className="field">Kind
          <select value={kind} onChange={(e) => setKind(e.target.value as "api" | "ui")} disabled={!enabled}>
            <option value="api">API</option>
            <option value="ui">Web UI</option>
          </select>
        </label>
      </div>
      <label className="field" style={{ marginTop: 12 }}>Requirements (user stories + acceptance criteria)
        <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={!enabled}
          placeholder="## US-1 ...&#10;- AC-1.1 ..." />
      </label>
      <div className="toolbar" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={!enabled || busy || text.length < 20} onClick={go}>
          {busy ? "Agent working…" : "Generate test cases"}
        </button>
        <span className="muted small">Allowed targets: {allowedHosts.join(", ")}</span>
      </div>
      {error && <p className="error small">{error}</p>}
      {events.length > 0 && <Trace events={events} />}
    </div>
  );
}
