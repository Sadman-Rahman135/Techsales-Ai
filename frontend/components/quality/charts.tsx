"use client";

import { Table2, BarChart3 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { type Daily, shortDay } from "@/lib/quality";

// design-system/MASTER.md §10: validated --mark-* tokens only; the series color and status colors never share a
// chart; every chart has a table twin; gridlines 1px solid; bars ≤ 24px with a 4px rounded data end; text never
// wears a mark color. "Not in documents" uses --mark-target (neutral) plus a hatch, so no new color is added.

export function ChartCard({
  title,
  summary,
  chart,
  table,
  legend,
}: {
  title: string;
  summary: string;
  chart: ReactNode;
  table: ReactNode;
  legend?: ReactNode;
}) {
  const id = useId();
  const [asTable, setAsTable] = useState(false);
  return (
    <section aria-labelledby={`${id}-title`} className="rounded-card border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 id={`${id}-title`} className="font-heading text-base font-semibold">
          {title}
        </h2>
        <button
          type="button"
          aria-pressed={asTable}
          onClick={() => setAsTable(!asTable)}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-control px-3 text-sm font-semibold text-accent hover:bg-accent/10"
        >
          {asTable ? <BarChart3 className="size-4" aria-hidden="true" /> : <Table2 className="size-4" aria-hidden="true" />}
          {asTable ? "View as chart" : "View as table"}
        </button>
      </div>
      <p className="mt-1 text-sm text-fg-muted">{summary}</p>
      {asTable ? (
        <div className="mt-3 overflow-x-auto">{table}</div>
      ) : (
        <figure className="mt-3" aria-label={summary}>
          {chart}
          {legend ? <figcaption className="mt-2">{legend}</figcaption> : null}
        </figure>
      )}
    </section>
  );
}

const W = 640;
const H = 220;
const PAD = { top: 12, right: 8, bottom: 28, left: 36 };

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

/** Answered (solid --mark-pass) stacked under not-in-documents (hatched --mark-target), one bar per day. */
export function OutcomeBars({ daily }: { daily: Daily[] }) {
  const patternId = useId();
  const max = niceMax(Math.max(0, ...daily.map((d) => d.answered + d.insufficient_evidence)));
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / Math.max(1, daily.length);
  const bar = Math.min(24, slot * 0.6);
  const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(daily.length / 10);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-hidden="true">
      <defs>
        <pattern id={patternId} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="var(--color-surface)" />
          <rect width="3" height="6" fill="var(--color-mark-target)" />
        </pattern>
      </defs>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--color-mark-track)" strokeWidth="1" />
          <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="fill-fg-muted text-[11px] tabular-nums">
            {t}
          </text>
        </g>
      ))}
      {daily.map((d, i) => {
        const x = PAD.left + slot * i + (slot - bar) / 2;
        const answeredTop = y(d.answered);
        const missingTop = y(d.answered + d.insufficient_evidence);
        return (
          <g key={d.date}>
            {d.answered > 0 ? (
              <rect x={x} y={answeredTop} width={bar} height={y(0) - answeredTop} fill="var(--color-mark-pass)" rx={d.insufficient_evidence ? 0 : 4} />
            ) : null}
            {d.insufficient_evidence > 0 ? (
              <rect
                x={x}
                y={missingTop}
                width={bar}
                height={answeredTop - missingTop}
                fill={`url(#${patternId})`}
                stroke="var(--color-mark-target)"
                strokeWidth="1"
                rx="4"
              />
            ) : null}
            {i % labelEvery === 0 ? (
              <text x={x + bar / 2} y={H - 8} textAnchor="middle" className="fill-fg-muted text-[11px]">
                {shortDay(d.date)}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

export function OutcomeLegend() {
  return (
    <ul className="flex flex-wrap gap-4 text-sm text-fg-muted">
      <li className="flex items-center gap-2">
        <svg width="14" height="14" aria-hidden="true">
          <rect width="14" height="14" rx="3" fill="var(--color-mark-pass)" />
        </svg>
        Answered from sources
      </li>
      <li className="flex items-center gap-2">
        <svg width="14" height="14" aria-hidden="true">
          <defs>
            <pattern id="legend-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="4" height="4" fill="var(--color-surface)" />
              <rect width="2" height="4" fill="var(--color-mark-target)" />
            </pattern>
          </defs>
          <rect width="14" height="14" rx="3" fill="url(#legend-hatch)" stroke="var(--color-mark-target)" />
        </svg>
        Not in our documents
      </li>
    </ul>
  );
}

/** One series (--mark-series), values written beside each bar. */
export function HorizontalBars({ items }: { items: { label: string; value: number }[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.label}>
          <div className="flex justify-between gap-3 text-sm">
            <span>{item.label}</span>
            <span className="tabular-nums text-fg-muted">{item.value}</span>
          </div>
          <svg viewBox="0 0 100 8" preserveAspectRatio="none" className="mt-1 h-2 w-full" aria-hidden="true">
            <rect width="100" height="8" rx="4" fill="var(--color-mark-track)" />
            <rect width={(item.value / max) * 100} height="8" rx="4" fill="var(--color-mark-series)" />
          </svg>
        </li>
      ))}
    </ul>
  );
}

/** KPI sparkline: --mark-series, 2px round-capped line, end dot 8px with a 2px surface ring. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const w = 120;
  const h = 32;
  if (values.length < 2) return null;
  const max = Math.max(1, ...values);
  const points = values.map((v, i) => [4 + (i / (values.length - 1)) * (w - 8), h - 5 - (v / max) * (h - 10)] as const);
  const [lx, ly] = points[points.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-8 w-[120px]" role="img" aria-label={label}>
      <polyline
        points={points.map(([x, y]) => `${x},${y}`).join(" ")}
        fill="none"
        stroke="var(--color-mark-series)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lx} cy={ly} r="4" fill="var(--color-mark-series)" stroke="var(--color-surface)" strokeWidth="2" />
    </svg>
  );
}

/** A daily percentage as one --mark-series line (0–100%). Days with no data leave a gap; each day with data gets
 * an 8px dot with a 2px surface ring, so a lone day still shows. */
export function ShareLine({ points }: { points: { date: string; value: number | null }[] }) {
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / Math.max(1, points.length);
  const x = (i: number) => PAD.left + slot * i + slot / 2;
  const y = (v: number) => PAD.top + innerH - (v / 100) * innerH;
  const labelEvery = Math.ceil(points.length / 10);
  const runs: [number, number][][] = [];
  let run: [number, number][] = [];
  points.forEach((p, i) => {
    if (p.value === null) {
      if (run.length) runs.push(run);
      run = [];
    } else {
      run.push([x(i), y(p.value)]);
    }
  });
  if (run.length) runs.push(run);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-hidden="true">
      {[0, 50, 100].map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--color-mark-track)" strokeWidth="1" />
          <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="fill-fg-muted text-[11px] tabular-nums">
            {t}%
          </text>
        </g>
      ))}
      {runs.map((r) =>
        r.length > 1 ? (
          <polyline
            key={`${r[0][0]}`}
            points={r.map(([px, py]) => `${px},${py}`).join(" ")}
            fill="none"
            stroke="var(--color-mark-series)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : null,
      )}
      {points.map((p, i) => (
        <g key={p.date}>
          {p.value !== null ? (
            <circle cx={x(i)} cy={y(p.value)} r="4" fill="var(--color-mark-series)" stroke="var(--color-surface)" strokeWidth="2" />
          ) : null}
          {i % labelEvery === 0 ? (
            <text x={x(i)} y={H - 8} textAnchor="middle" className="fill-fg-muted text-[11px]">
              {shortDay(p.date)}
            </text>
          ) : null}
        </g>
      ))}
    </svg>
  );
}
