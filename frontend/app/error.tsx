"use client";

import { LifeBuoy, RotateCw, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { SUPPORT_HREF } from "@/lib/support";

// Any screen without its own error boundary (content-guide.md "Error": what happened + what to do; the
// reference is shown, never the technical detail).
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-16">
      <div role="alert" className="rounded-card border border-border border-l-4 border-l-danger bg-surface p-6">
        <h1 className="flex items-center gap-2 font-heading text-xl font-semibold">
          <TriangleAlert className="size-5 text-danger" aria-hidden="true" />
          Something went wrong
        </h1>
        <p className="mt-2 text-fg-muted">This page didn&apos;t load. Try again in a moment.</p>
        {error.digest ? <p className="mt-2 font-mono text-xs text-fg-muted">Reference: {error.digest}</p> : null}
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={reset}
            className="inline-flex min-h-11 items-center gap-2 rounded-control bg-accent px-4 font-semibold text-on-accent hover:bg-accent/90"
          >
            <RotateCw className="size-4" aria-hidden="true" />
            Try again
          </button>
          <Link
            href={SUPPORT_HREF}
            className="inline-flex min-h-11 items-center gap-2 rounded-control border border-border-strong px-4 font-semibold hover:bg-surface-muted"
          >
            <LifeBuoy className="size-4" aria-hidden="true" />
            Contact support
          </Link>
        </div>
      </div>
    </main>
  );
}
