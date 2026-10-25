import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { DocumentsView, type ListQuery } from "@/components/admin/documents-view";
import { NoPermission } from "@/components/admin/no-permission";
import { canManageDocuments, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import { type DocumentPage, type Facets, METADATA_FILTERS } from "@/lib/documents";

export const metadata: Metadata = { title: "Documents" };

const SORTS = new Set(["updated", "-updated", "title", "-title"]);
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const PATH = "/admin/documents";

export default function DocumentsPage({ searchParams }: PageProps<"/admin/documents">) {
  return (
    <AdminShell current={PATH}>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
        {/* Session and query string are request data: they stream in behind this boundary. */}
        <Suspense fallback={<ListSkeleton />}>
          <DocumentsSection searchParams={searchParams} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

async function DocumentsSection({ searchParams }: { searchParams: PageProps<"/admin/documents">["searchParams"] }) {
  const me = await requireUser(PATH);
  if (!canManageDocuments(me)) return <NoPermission />;

  const params = await searchParams;
  const query: ListQuery = {
    q: first(params.q).slice(0, 200),
    audience: ["public", "employee"].includes(first(params.audience)) ? first(params.audience) : "",
    tag: first(params.tag).slice(0, 40),
    sort: SORTS.has(first(params.sort)) ? first(params.sort) : "-updated",
    offset: Math.max(0, Number.parseInt(first(params.offset), 10) || 0),
    brand: first(params.brand).slice(0, 120),
    category: first(params.category).slice(0, 120),
    department: first(params.department).slice(0, 120),
    language: /^[a-z]{2}$/.test(first(params.language)) ? first(params.language) : "",
    from: DATE.test(first(params.from)) ? first(params.from) : "",
    to: DATE.test(first(params.to)) ? first(params.to) : "",
  };
  const api = new URLSearchParams({ sort: query.sort, limit: "25", offset: String(query.offset) });
  if (query.q) api.set("q", query.q);
  if (query.audience) api.set("audience", query.audience);
  if (query.tag) api.set("tag", query.tag);
  for (const key of METADATA_FILTERS) {
    if (query[key]) api.set(key === "from" ? "effective_from" : key === "to" ? "effective_to" : key, query[key]);
  }

  const [response, facetsResponse] = await Promise.all([
    backendFetch(`/api/v1/documents?${api}`),
    backendFetch("/api/v1/documents/facets"),
  ]);
  if (response.status === 401) redirect(`/login?next=${encodeURIComponent(PATH)}`);
  if (!response.ok) throw new Error(`document list failed: HTTP ${response.status}`);
  const page = (await response.json()) as DocumentPage;
  const facets: Facets = facetsResponse.ok
    ? ((await facetsResponse.json()) as Facets)
    : { product_models: [], tags: [] };
  // "Updated 5 minutes ago" is measured against the API's clock (its Date header), not this render.
  const renderedAt = Date.parse(response.headers.get("date") ?? "") || Date.parse(page.items[0]?.updated_at ?? "") || 0;

  return (
    <DocumentsView
      page={page}
      query={query}
      tags={facets.tags}
      facets={facets}
      isAdmin={me.role === "admin"}
      timezone={me.organization.timezone}
      renderedAt={renderedAt}
    />
  );
}

function ListSkeleton() {
  return (
    <div aria-hidden="true" className="space-y-4">
      <div className="h-8 w-48 animate-pulse rounded-control bg-surface-muted" />
      <div className="h-11 animate-pulse rounded-control bg-surface-muted" />
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="h-11 animate-pulse rounded-control bg-surface-muted" />
      ))}
    </div>
  );
}
