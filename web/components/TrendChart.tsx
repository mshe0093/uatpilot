"use client";

import { useState } from "react";
import type { Batch } from "@/lib/types";
import { OUTCOMES } from "./Status";

// Stacked bars, one per run batch: passed / regression / flaky / environment / other failures.
// One axis (count of test cases), status colours with a legend, 2px surface gap between
// segments, 4px rounded top, and a per-bar hover tooltip with the exact numbers.
const SERIES = [
  { key: "passed", outcome: "pass" },
  { key: "regressions", outcome: "real_regression" },
  { key: "flaky", outcome: "flaky" },
  { key: "env_issues", outcome: "environment_issue" },
  { key: "test_defects", outcome: "test_defect" },
  { key: "untriaged", outcome: "untriaged" },
] as const;

type Row = Batch & { untriaged: number };

const H = 200;
const PAD = { top: 10, right: 8, bottom: 26, left: 32 };

export function TrendChart({ batches }: { batches: Batch[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (batches.length === 0) {
    return <p className="muted small">No runs yet. Press “Run suite” to record the first one.</p>;
  }
  const rows: Row[] = batches.map((b) => ({
    ...b,
    untriaged: Math.max(0, b.failed - b.regressions - b.flaky - b.env_issues - b.test_defects),
  }));
  const maxTotal = Math.max(...rows.map((r) => r.total), 1);
  const width = Math.max(320, rows.length * 44 + PAD.left + PAD.right);
  const plotW = width - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const slot = plotW / rows.length;
  const barW = Math.min(28, slot * 0.6);
  const y = (v: number) => PAD.top + plotH - (v / maxTotal) * plotH;
  const ticks = [0, Math.round(maxTotal / 2), maxTotal];
  const present = SERIES.filter((s) => rows.some((r) => r[s.key] > 0));

  return (
    <div>
      <div className="legend" role="list">
        {present.map((s) => (
          <span key={s.key} role="listitem">
            <span className="dot" style={{ background: OUTCOMES[s.outcome].color }} />
            {OUTCOMES[s.outcome].icon} {OUTCOMES[s.outcome].label}
          </span>
        ))}
      </div>
      <div className="chart-wrap" style={{ overflowX: "auto" }} onMouseLeave={() => setHover(null)}>
        <svg width={width} height={H} role="img" aria-label="Test results per run, oldest to newest">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)}
                stroke={t === 0 ? "var(--axis)" : "var(--grid)"} strokeWidth={1} />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{t}</text>
            </g>
          ))}
          {rows.map((r, i) => {
            const cx = PAD.left + slot * i + slot / 2;
            let acc = 0;
            const segs = SERIES.filter((s) => r[s.key] > 0);
            return (
              <g key={r.id}>
                {segs.map((s, j) => {
                  const v = r[s.key];
                  const top = y(acc + v);
                  const bottom = y(acc);
                  acc += v;
                  const isTop = j === segs.length - 1;
                  // 2px surface gap between stacked segments
                  const h = Math.max(1, bottom - top - (j > 0 ? 2 : 0));
                  return (
                    <path key={s.key} d={barPath(cx - barW / 2, top, barW, h, isTop ? 4 : 0)}
                      fill={OUTCOMES[s.outcome].color} opacity={hover === null || hover === i ? 1 : 0.45} />
                  );
                })}
                {(i === 0 || i === rows.length - 1 || rows.length <= 8) && (
                  <text x={cx} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">
                    #{i + 1}
                  </text>
                )}
                {/* hit target wider than the bar */}
                <rect x={cx - slot / 2} y={PAD.top} width={slot} height={plotH} fill="transparent"
                  onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                  aria-label={`Run ${i + 1}: ${r.passed} of ${r.total} passed`} />
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="tooltip" style={{
            left: Math.min(PAD.left + slot * hover + slot / 2 + 12, width - 190), top: 4 }}>
            <strong>Run #{hover + 1}</strong>
            <div className="muted">{new Date(rows[hover].started_at).toLocaleString()}</div>
            {SERIES.map((s) => (
              <div className="row" key={s.key}>
                <span>{OUTCOMES[s.outcome].icon} {OUTCOMES[s.outcome].label}</span>
                <span>{rows[hover][s.key]}</span>
              </div>
            ))}
            <div className="row"><span>Total</span><span>{rows[hover].total}</span></div>
          </div>
        )}
      </div>
    </div>
  );
}

function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, h, w / 2);
  if (rr <= 0) return `M${x},${y}h${w}v${h}h${-w}Z`;
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}
