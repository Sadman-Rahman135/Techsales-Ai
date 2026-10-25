import { SearchX } from "lucide-react";
import Link from "next/link";

// Also what hidden resources look like (§9.3: a 404 never says whether something exists).
export default function NotFound() {
  return (
    <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-16">
      <div className="rounded-card border border-border bg-surface p-6">
        <h1 className="flex items-center gap-2 font-heading text-2xl font-semibold">
          <SearchX className="size-6 text-fg-muted" aria-hidden="true" />
          We couldn&apos;t find that page
        </h1>
        <p className="mt-2 text-fg-muted">
          It may have been deleted, or the link may be wrong or meant for another account.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Link
            href="/chat"
            className="inline-flex min-h-11 items-center rounded-control bg-accent px-4 font-semibold text-on-accent hover:bg-accent/90"
          >
            Ask TechCare
          </Link>
          <Link
            href="/login"
            className="inline-flex min-h-11 items-center rounded-control border border-border-strong px-4 font-semibold hover:bg-surface-muted"
          >
            Sign in
          </Link>
        </div>
      </div>
    </main>
  );
}
