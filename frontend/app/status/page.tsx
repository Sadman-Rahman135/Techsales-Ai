import { BadgeCheck, CircleCheck, CircleX, Database, HardDrive, Layers, RotateCw, TriangleAlert } from "lucide-react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { SiteHeader } from "@/components/site-header";
import { API_BASE_URL, type ComponentCheck, getHealth } from "@/lib/api";

export const metadata: Metadata = { title: "System status" };

// Developer page for blueprint §5.1 ("PostgreSQL and Chroma running locally"). Never served in production:
// it would reveal internal topology.
export default function StatusPage() {
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <>
      <SiteHeader badge="Developer" />
      <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
        <h1 className="font-heading text-2xl font-semibold">System status</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Local development stack, checked through the API at <code className="font-mono">{API_BASE_URL}</code>
        </p>
        <Suspense fallback={<StatusSkeleton />}>
          <LiveStatus />
        </Suspense>
      </main>
    </>
  );
}

const COMPONENTS: Record<string, { label: string; description: string; icon: typeof Database }> = {
  postgres: { label: "PostgreSQL", description: "Source of truth: users, documents, versions", icon: Database },
  chroma: { label: "Chroma", description: "Vector index, rebuildable from Postgres", icon: Layers },
  storage: { label: "File storage", description: "Original uploaded documents", icon: HardDrive },
};

async function LiveStatus() {
  await connection(); // fresh on every request, never prerendered or cached
  const result = await getHealth();

  if (!result.reachable) {
    return (
      <section
        role="alert"
        className="mt-8 rounded-card border border-border border-l-4 border-l-danger bg-surface p-5"
      >
        <h2 className="flex items-center gap-2 font-heading text-lg font-semibold text-danger">
          <TriangleAlert className="size-5" aria-hidden="true" />
          Can&apos;t reach the API
        </h2>
        <p className="mt-1 text-fg-muted">{result.reason}. Start the local stack, then check again:</p>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">
          <li>
            <code className="font-mono">docker compose up -d</code>
          </li>
          <li>
            <code className="font-mono">cd backend; .venv/Scripts/fastapi dev</code>
          </li>
        </ol>
        <CheckAgain />
      </section>
    );
  }

  const { report } = result;
  const down = report.checks.filter((check) => !check.ok);
  const healthy = report.status === "ok";

  return (
    <>
      <section
        aria-live="polite"
        className={`mt-8 flex items-start gap-3 rounded-card border border-border border-l-4 bg-surface p-5 ${healthy ? "border-l-success" : "border-l-danger"}`}
      >
        {healthy ? (
          <BadgeCheck className="mt-0.5 size-6 shrink-0 text-success" aria-hidden="true" />
        ) : (
          <TriangleAlert className="mt-0.5 size-6 shrink-0 text-danger" aria-hidden="true" />
        )}
        <div>
          <h2 className="font-heading text-lg font-semibold">
            {healthy ? "All systems working" : `${down.length} of ${report.checks.length} components down`}
          </h2>
          <p className="text-sm text-fg-muted">
            Environment <code className="font-mono">{report.app_env}</code>
          </p>
        </div>
      </section>

      <ul className="mt-4 divide-y divide-border overflow-hidden rounded-card border border-border bg-surface">
        {report.checks.map((check) => (
          <ComponentRow key={check.name} check={check} />
        ))}
      </ul>
      <CheckAgain />
    </>
  );
}

function ComponentRow({ check }: { check: ComponentCheck }) {
  const meta = COMPONENTS[check.name] ?? { label: check.name, description: "", icon: Database };
  const Icon = meta.icon;
  return (
    <li className="flex items-center gap-4 px-5 py-4">
      <span
        aria-hidden="true"
        className="grid size-10 shrink-0 place-items-center rounded-control bg-surface-muted text-accent"
      >
        <Icon className="size-5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{meta.label}</p>
        <p className="text-sm text-fg-muted">{meta.description}</p>
      </div>
      <div className="text-right">
        <p
          className={`flex items-center justify-end gap-1.5 text-sm font-semibold ${check.ok ? "text-success" : "text-danger"}`}
        >
          {check.ok ? (
            <CircleCheck className="size-4" aria-hidden="true" />
          ) : (
            <CircleX className="size-4" aria-hidden="true" />
          )}
          {check.ok ? "Working" : "Down"}
        </p>
        <p className="text-xs text-fg-muted tabular-nums">{check.ok ? `${check.latency_ms} ms` : check.detail}</p>
      </div>
    </li>
  );
}

function CheckAgain() {
  return (
    <a
      href="/status"
      className="mt-6 inline-flex min-h-11 items-center gap-2 rounded-control border border-border-strong bg-surface px-4 font-semibold transition-colors duration-150 hover:bg-surface-muted"
    >
      <RotateCw className="size-4" aria-hidden="true" />
      Check again
    </a>
  );
}

function StatusSkeleton() {
  return (
    <div aria-busy="true" aria-label="Checking components" className="mt-8 space-y-4">
      <div className="h-[86px] animate-pulse rounded-card bg-surface-muted" />
      <div className="h-[230px] animate-pulse rounded-card bg-surface-muted" />
    </div>
  );
}
