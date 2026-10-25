"use client";

import { RotateCw, TriangleAlert } from "lucide-react";

export default function DocumentsError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
      <div role="alert" className="rounded-card border border-border border-l-4 border-l-danger bg-surface p-5">
        <h1 className="flex items-center gap-2 font-heading text-lg font-semibold">
          <TriangleAlert className="size-5 text-danger" aria-hidden="true" />
          We couldn&apos;t load the documents
        </h1>
        <p className="mt-1 text-fg-muted">
          The document service didn&apos;t respond. Your documents are safe; try again in a moment.
        </p>
        <button
          type="button"
          onClick={reset}
          className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-control border border-border-strong px-4 font-semibold hover:bg-surface-muted"
        >
          <RotateCw className="size-4" aria-hidden="true" />
          Try again
        </button>
      </div>
    </main>
  );
}
