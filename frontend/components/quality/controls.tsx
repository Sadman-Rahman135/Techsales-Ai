"use client";

import { CalendarRange, LoaderCircle, RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, useTransition } from "react";

import type { ApiError } from "@/lib/documents";
import type { DateRange, Preset } from "@/lib/quality";

const PRESETS: { key: Exclude<Preset, "custom">; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
];

/** quality-dashboard.md: always visible, stored in the URL, with an explicit label (rendered by the page). */
export function RangeControl({ range, label }: { range: DateRange; label: string }) {
  const id = useId();
  const router = useRouter();
  const [custom, setCustom] = useState(range.preset === "custom");
  const [start, setStart] = useState(range.start);
  const [end, setEnd] = useState(range.end);
  const [pending, startTransition] = useTransition();
  const tab = (active: boolean) =>
    `inline-flex min-h-11 items-center rounded-control px-3 text-sm font-semibold ${
      active ? "bg-accent text-on-accent" : "text-fg hover:bg-surface-muted"
    }`;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <nav aria-label="Time range" className="flex flex-wrap gap-1 rounded-card border border-border bg-surface p-1">
          {PRESETS.map((p) => (
            <Link
              key={p.key}
              href={`?range=${p.key}`}
              aria-current={range.preset === p.key && !custom ? "true" : undefined}
              onClick={() => setCustom(false)}
              className={tab(range.preset === p.key && !custom)}
            >
              {p.label}
            </Link>
          ))}
          <button type="button" aria-expanded={custom} onClick={() => setCustom(!custom)} className={tab(custom)}>
            <CalendarRange className="mr-1.5 size-4" aria-hidden="true" />
            Custom
          </button>
        </nav>
        {pending ? <LoaderCircle className="size-4 animate-spin text-fg-muted" aria-label="Loading" /> : null}
      </div>
      {custom ? (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            startTransition(() => router.push(`?range=custom&start=${start}&end=${end}`));
          }}
        >
          <div>
            <label htmlFor={`${id}-start`} className="block text-sm font-semibold">
              From
            </label>
            <input
              id={`${id}-start`}
              type="date"
              value={start}
              max={end}
              onChange={(e) => setStart(e.target.value)}
              className="mt-1 min-h-11 rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
            />
          </div>
          <div>
            <label htmlFor={`${id}-end`} className="block text-sm font-semibold">
              To
            </label>
            <input
              id={`${id}-end`}
              type="date"
              value={end}
              min={start}
              onChange={(e) => setEnd(e.target.value)}
              className="mt-1 min-h-11 rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
            />
          </div>
          <button
            type="submit"
            className="inline-flex min-h-11 items-center rounded-control border border-border-strong px-4 font-semibold hover:bg-surface-muted"
          >
            Apply
          </button>
        </form>
      ) : null}
      <p className="text-sm font-semibold text-fg-muted">{label}</p>
    </div>
  );
}

export function RetryJob({ jobId, title }: { jobId: string; title: string }) {
  const router = useRouter();
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");
  const [message, setMessage] = useState("");
  async function retry() {
    setState("busy");
    const response = await fetch(`/api/admin/ingestion-jobs/${jobId}/retry`, { method: "POST" }).catch(() => null);
    if (response?.ok) {
      setState("done");
      router.refresh();
      return;
    }
    setMessage(response ? ((await response.json()) as { error: ApiError }).error.message : "That didn't reach us.");
    setState("failed");
  }
  if (state === "done") return <span role="status" className="text-sm text-fg-muted">Queued again</span>;
  return (
    <span className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={retry}
        disabled={state === "busy"}
        aria-label={`Retry processing ${title}`}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-control px-3 text-sm font-semibold text-accent hover:bg-accent/10 disabled:opacity-60"
      >
        {state === "busy" ? (
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        ) : (
          <RotateCw className="size-4" aria-hidden="true" />
        )}
        Retry
      </button>
      {state === "failed" ? (
        <span role="alert" className="max-w-56 text-xs text-danger">
          {message}
        </span>
      ) : null}
    </span>
  );
}
