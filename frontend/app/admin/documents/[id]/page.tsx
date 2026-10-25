import { ChevronLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { DocumentDetailView } from "@/components/admin/document-detail";
import { NoPermission } from "@/components/admin/no-permission";
import { canManageDocuments, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import type { ActivityEvent, DocumentDetail } from "@/lib/documents";

export const metadata: Metadata = { title: "Document" };

// `/admin/documents/[id]` (admin-documents.md "Document detail"): version timeline, publish, preview, chunk
// diagnostics, activity, delete.
export default function DocumentPage({ params, searchParams }: PageProps<"/admin/documents/[id]">) {
  return (
    <AdminShell current="/admin/documents">
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 outline-none sm:px-6 lg:py-8">
        <Link
          href="/admin/documents"
          className="-ml-2 inline-flex min-h-11 items-center gap-1 rounded-control px-2 text-sm font-semibold text-fg-muted hover:text-fg"
        >
          <ChevronLeft className="size-4" aria-hidden="true" />
          Documents
        </Link>
        <Suspense fallback={<DetailSkeleton />}>
          <Detail params={params} searchParams={searchParams} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

async function Detail({
  params,
  searchParams,
}: {
  params: PageProps<"/admin/documents/[id]">["params"];
  searchParams: PageProps<"/admin/documents/[id]">["searchParams"];
}) {
  const { id } = await params;
  const path = `/admin/documents/${id}`;
  const me = await requireUser(path);
  if (!canManageDocuments(me)) return <NoPermission />;

  const [response, activityResponse] = await Promise.all([
    backendFetch(`/api/v1/documents/${encodeURIComponent(id)}`),
    backendFetch(`/api/v1/documents/${encodeURIComponent(id)}/activity`),
  ]);
  if (response.status === 401) redirect(`/login?next=${encodeURIComponent(path)}`);
  if (response.status === 404 || response.status === 422) notFound();
  if (!response.ok) throw new Error(`document detail failed: HTTP ${response.status}`);
  const document = (await response.json()) as DocumentDetail;
  const activity = activityResponse.ok ? ((await activityResponse.json()) as ActivityEvent[]) : [];
  const renderedAt = Date.parse(response.headers.get("date") ?? "") || Date.parse(document.updated_at);
  const query = await searchParams;
  const tab = ["preview", "chunks", "activity"].includes(String(query.tab)) ? String(query.tab) : "preview";
  const version = typeof query.version === "string" ? query.version : null;

  return (
    <DocumentDetailView
      document={document}
      activity={activity}
      isAdmin={me.role === "admin"}
      timezone={me.organization.timezone}
      renderedAt={renderedAt}
      initialTab={tab as "preview" | "chunks" | "activity"}
      initialVersion={version}
    />
  );
}

function DetailSkeleton() {
  return (
    <div aria-hidden="true" className="mt-4 space-y-4">
      <div className="h-8 w-80 animate-pulse rounded-control bg-surface-muted" />
      <div className="h-5 w-64 animate-pulse rounded-control bg-surface-muted" />
      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <div className="h-72 animate-pulse rounded-card bg-surface-muted" />
        <div className="h-96 animate-pulse rounded-card bg-surface-muted" />
      </div>
    </div>
  );
}
