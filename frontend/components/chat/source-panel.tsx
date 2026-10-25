"use client";

import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Lock, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { bffPreviewUrl, type Citation, pageLabel } from "@/lib/chat";
import type { Preview } from "@/lib/documents";

// MASTER.md §7 "Source panel": right drawer ≥1024px (the chat column shifts, not overlaid), bottom sheet below.
// Title, version, page, the quoted passage, and "Open document" only when the API authorizes the caller.
type Loaded = { state: "loading" } | { state: "ready"; preview: Preview } | { state: "hidden" } | { state: "error" };

const INLINE = new Set(["application/pdf", "text/plain"]);

export function SourcePanel({
  citations,
  position,
  onMove,
  onClose,
}: {
  citations: Citation[];
  position: number;
  onMove: (position: number) => void;
  onClose: () => void;
}) {
  const ordered = [...citations].sort((a, b) => a.position - b.position);
  const index = Math.max(0, ordered.findIndex((c) => c.position === position));
  const citation = ordered[index];
  const heading = useRef<HTMLHeadingElement>(null);
  const [loaded, setLoaded] = useState<{ key: string; value: Loaded } | null>(null);
  const key = `${citation?.version_id}:${citation?.page_start}`;

  useEffect(() => {
    heading.current?.focus();
  }, [position]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const url = citation?.preview_url ? bffPreviewUrl(citation.preview_url) : null;
    if (!url) return;
    let live = true;
    fetch(url)
      .then(async (response) => {
        if (!live) return;
        if (response.status === 404 || response.status === 403 || response.status === 401) {
          setLoaded({ key, value: { state: "hidden" } });
          return;
        }
        if (!response.ok) throw new Error(String(response.status));
        setLoaded({ key, value: { state: "ready", preview: (await response.json()) as Preview } });
      })
      .catch(() => live && setLoaded({ key, value: { state: "error" } }));
    return () => {
      live = false;
    };
  }, [citation?.preview_url, key]);

  if (!citation) return null;
  const current: Loaded = !citation.preview_url
    ? { state: "hidden" }
    : loaded?.key === key
      ? loaded.value
      : { state: "loading" };

  return (
    <aside
      aria-labelledby="source-panel-title"
      className="flex h-full flex-col overflow-hidden bg-surface"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
        <h2
          id="source-panel-title"
          ref={heading}
          tabIndex={-1}
          className="font-heading text-base font-semibold outline-none"
        >
          Source {index + 1} of {ordered.length}
        </h2>
        <div className="flex items-center">
          <button
            type="button"
            aria-label="Previous source"
            disabled={index === 0}
            onClick={() => onMove(ordered[index - 1].position)}
            className="grid size-11 place-items-center rounded-control text-fg-muted hover:bg-surface-muted hover:text-fg disabled:opacity-40"
          >
            <ChevronLeft className="size-5" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Next source"
            disabled={index === ordered.length - 1}
            onClick={() => onMove(ordered[index + 1].position)}
            className="grid size-11 place-items-center rounded-control text-fg-muted hover:bg-surface-muted hover:text-fg disabled:opacity-40"
          >
            <ChevronRight className="size-5" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Close source"
            onClick={onClose}
            className="grid size-11 place-items-center rounded-control text-fg-muted hover:bg-surface-muted hover:text-fg"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <div>
          <p className="font-semibold">
            [{citation.position}] {citation.title ?? "This document was removed"}
          </p>
          <p className="mt-0.5 text-sm text-fg-muted">
            {[
              current.state === "ready" ? `Version ${current.preview.version_number}` : null,
              pageLabel(citation),
              citation.section?.split(" > ").at(-1) ?? null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {citation.audience === "employee" ? (
            <p className="mt-2 inline-flex items-center gap-1 rounded-full bg-warning/12 px-2 py-0.5 text-xs font-semibold">
              <Lock className="size-3.5 text-warning" aria-hidden="true" />
              Employee only: don&apos;t read it out to customers word for word
            </p>
          ) : null}
        </div>

        {citation.quote ? (
          <figure>
            <figcaption className="text-xs font-semibold text-fg-muted">Quoted passage</figcaption>
            <blockquote className="mt-1 rounded-control border-l-4 border-accent bg-accent/8 p-3 text-base">
              <mark className="bg-transparent text-fg">{citation.quote}</mark>
            </blockquote>
          </figure>
        ) : null}

        {current.state === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted">
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            Loading the document…
          </p>
        ) : null}
        {current.state === "error" ? (
          <p role="alert" className="text-sm text-fg-muted">
            We couldn&apos;t load the document right now. The quoted passage above is what the answer used.
          </p>
        ) : null}
        {current.state === "ready" ? (
          <>
            {!current.preview.is_active_version ? (
              <p className="text-sm text-warning">
                <span className="text-fg">This citation points to an older version of the document.</span>
              </p>
            ) : null}
            <a
              href={current.preview.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-2 rounded-control border border-border-strong px-4 font-semibold text-fg hover:bg-surface-muted"
            >
              <ExternalLink className="size-4" aria-hidden="true" />
              {INLINE.has(current.preview.mime_type) ? "Open document" : "Download document"}
              <span className="sr-only">(opens in a new tab)</span>
            </a>
            {INLINE.has(current.preview.mime_type) ? (
              <iframe
                src={current.preview.url}
                title={`${current.preview.title}, ${pageLabel(citation) ?? "document"}`}
                className="h-[55dvh] w-full rounded-control border border-border bg-surface-muted"
              />
            ) : null}
          </>
        ) : null}
      </div>
    </aside>
  );
}
